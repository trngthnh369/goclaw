package methods

import (
	"context"
	"encoding/json"
	"log/slog"

	"github.com/nextlevelbuilder/goclaw/internal/bus"
	"github.com/nextlevelbuilder/goclaw/internal/gateway"
	"github.com/nextlevelbuilder/goclaw/internal/i18n"
	"github.com/nextlevelbuilder/goclaw/internal/store"
	"github.com/nextlevelbuilder/goclaw/internal/tools"
	"github.com/nextlevelbuilder/goclaw/pkg/protocol"
)

// ExecApprovalMethods handles exec.approval.list, exec.approval.approve, exec.approval.deny.
type ExecApprovalMethods struct {
	manager  *tools.ExecApprovalManager
	eventBus bus.EventPublisher
}

func NewExecApprovalMethods(manager *tools.ExecApprovalManager, eventBus bus.EventPublisher) *ExecApprovalMethods {
	return &ExecApprovalMethods{manager: manager, eventBus: eventBus}
}

func (m *ExecApprovalMethods) Register(router *gateway.MethodRouter) {
	router.Register(protocol.MethodApprovalsList, m.handleList)
	router.Register(protocol.MethodApprovalsApprove, m.handleApprove)
	router.Register(protocol.MethodApprovalsDeny, m.handleDeny)
}

func (m *ExecApprovalMethods) handleList(ctx context.Context, client *gateway.Client, req *protocol.RequestFrame) {
	if m.manager == nil {
		client.SendResponse(protocol.NewOKResponse(req.ID, map[string]any{
			"pending": []any{},
		}))
		return
	}
	// ctx carries the caller's tenant/master scope — the manager filters on it,
	// so an operator in one tenant cannot enumerate another tenant's approvals.
	pending := m.manager.ListPending(ctx)

	type pendingInfo struct {
		ID        string `json:"id"`
		Command   string `json:"command"`
		AgentID   string `json:"agentId"`
		CreatedAt int64  `json:"createdAt"`
	}

	items := make([]pendingInfo, 0, len(pending))
	for _, pa := range pending {
		items = append(items, pendingInfo{
			ID:        pa.ID,
			Command:   pa.Command,
			AgentID:   pa.AgentID,
			CreatedAt: pa.CreatedAt.UnixMilli(),
		})
	}

	client.SendResponse(protocol.NewOKResponse(req.ID, map[string]any{
		"pending": items,
	}))
}

func (m *ExecApprovalMethods) handleApprove(ctx context.Context, client *gateway.Client, req *protocol.RequestFrame) {
	locale := store.LocaleFromContext(ctx)
	if m.manager == nil {
		client.SendResponse(protocol.NewErrorResponse(req.ID, protocol.ErrInvalidRequest, i18n.T(locale, i18n.MsgExecApprovalDisabled)))
		return
	}

	var params struct {
		ID string `json:"id"`
		// Always is still accepted for wire compatibility but no longer grants a
		// standing allowlist entry: the old allow-always store was process-global,
		// cross-tenant and unrevocable. Every approval is now single-use.
		Always bool `json:"always"`
	}
	if req.Params != nil {
		json.Unmarshal(req.Params, &params)
	}

	if params.ID == "" {
		client.SendResponse(protocol.NewErrorResponse(req.ID, protocol.ErrInvalidRequest, i18n.T(locale, i18n.MsgRequired, "id")))
		return
	}

	if params.Always {
		slog.Warn("exec approval: allow-always requested but no longer supported; approving once",
			"id", params.ID)
	}

	decision := tools.ApprovalAllowOnce

	if err := m.manager.Resolve(ctx, params.ID, decision); err != nil {
		client.SendResponse(protocol.NewErrorResponse(req.ID, protocol.ErrNotFound, err.Error()))
		return
	}

	client.SendResponse(protocol.NewOKResponse(req.ID, map[string]any{
		"resolved": true,
		"decision": string(decision),
	}))
	emitAudit(m.eventBus, client, "exec.approved", "exec", params.ID)
}

func (m *ExecApprovalMethods) handleDeny(ctx context.Context, client *gateway.Client, req *protocol.RequestFrame) {
	locale := store.LocaleFromContext(ctx)
	if m.manager == nil {
		client.SendResponse(protocol.NewErrorResponse(req.ID, protocol.ErrInvalidRequest, i18n.T(locale, i18n.MsgExecApprovalDisabled)))
		return
	}

	var params struct {
		ID string `json:"id"`
	}
	if req.Params != nil {
		json.Unmarshal(req.Params, &params)
	}

	if params.ID == "" {
		client.SendResponse(protocol.NewErrorResponse(req.ID, protocol.ErrInvalidRequest, i18n.T(locale, i18n.MsgRequired, "id")))
		return
	}

	if err := m.manager.Resolve(ctx, params.ID, tools.ApprovalDeny); err != nil {
		client.SendResponse(protocol.NewErrorResponse(req.ID, protocol.ErrNotFound, err.Error()))
		return
	}

	client.SendResponse(protocol.NewOKResponse(req.ID, map[string]any{
		"resolved": true,
		"decision": "deny",
	}))
	emitAudit(m.eventBus, client, "exec.denied", "exec", params.ID)
}
