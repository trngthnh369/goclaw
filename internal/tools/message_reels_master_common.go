package tools

import (
	"context"
	"crypto/hmac"
	"crypto/rand"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"strings"
	"time"

	"github.com/google/uuid"

	"github.com/nextlevelbuilder/goclaw/internal/store"
)

// Shared by the Linux master-mode send path (message_reels_master.go) and the
// approval path (postReel), which also builds on other platforms.

const (
	reelsMasterArg  = "reels_master"
	reelsMasterLine = "master_sha256:"
	// reelsDraftMaxAge bounds a draft by its signed creation time; the store
	// sweep uses directory mtimes, which the agent can refresh.
	reelsDraftMaxAge = 96 * time.Hour
)

// reelsDraftKey signs draft records. It is generated per process and never
// leaves memory: an env-derived key would reach dumpable children that inherit
// the gateway env. A restart therefore invalidates pending master-mode drafts.
var reelsDraftKey = func() []byte {
	key := make([]byte, 32)
	if _, err := rand.Read(key); err != nil {
		panic("reels draft key: " + err.Error())
	}
	return key
}()

type reelsDraftRecord struct {
	V             int       `json:"v"`
	TenantID      string    `json:"tenant_id"`
	AgentKey      string    `json:"agent_key"`
	Channel       string    `json:"channel"`
	ChatID        string    `json:"chat_id"`
	Nonce         string    `json:"nonce"`
	CaptionSHA256 string    `json:"caption_sha256"`
	ReviewSHA256  string    `json:"review_sha256"`
	MasterSHA256  string    `json:"master_sha256"`
	MasterBytes   int64     `json:"master_bytes"`
	CreatedAt     time.Time `json:"created_at"`
	HMAC          string    `json:"hmac,omitempty"`
}

func (r reelsDraftRecord) mac() string {
	unsigned := r
	unsigned.HMAC = ""
	body, _ := json.Marshal(unsigned)
	m := hmac.New(sha256.New, reelsDraftKey)
	m.Write(body)
	return hex.EncodeToString(m.Sum(nil))
}

// reelsMasterSHALines returns the values of every master_sha256 line.
func reelsMasterSHALines(text string) []string {
	var out []string
	for _, line := range strings.Split(normalizeApprovalContent(text), "\n") {
		if v, ok := strings.CutPrefix(strings.TrimSpace(line), reelsMasterLine); ok {
			out = append(out, strings.TrimSpace(v))
		}
	}
	return out
}

func sha256Hex(s string) string {
	sum := sha256.Sum256([]byte(s))
	return hex.EncodeToString(sum[:])
}

// reelsTenant is the tenant a draft is recorded under and checked against.
func reelsTenant(ctx context.Context) string {
	if rc := store.RunContextFromCtx(ctx); rc != nil && rc.TenantID != uuid.Nil {
		return rc.TenantID.String()
	}
	return store.TenantIDFromContext(ctx).String()
}
