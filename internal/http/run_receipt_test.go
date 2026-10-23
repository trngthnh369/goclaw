package http

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/nextlevelbuilder/goclaw/internal/store"
	"github.com/nextlevelbuilder/goclaw/internal/tools"
)

func TestRunReceiptEndpointAnswersOnlyLoopbackCallersWithALiveToken(t *testing.T) {
	rc := &store.RunContext{RunID: "run-http", AgentKey: "vf-director", SenderID: "u1",
		ApprovalSenderAllowed: true, ReplyToMessageID: "m1", ReplyToAuthorID: "bot",
		ChannelBotUserID: "bot", CurrentMessage: "cứ làm tiếp"}
	tok := tools.RunReceiptToken(store.WithRunContext(context.Background(), rc))
	defer tools.ReleaseRunReceipt("run-http")
	mux := http.NewServeMux()
	RegisterRunReceiptRoute(mux)

	call := func(remote, auth string) *httptest.ResponseRecorder {
		req := httptest.NewRequest(http.MethodGet, "/v1/runs/receipt", nil)
		req.RemoteAddr = remote
		if auth != "" {
			req.Header.Set("Authorization", auth)
		}
		rec := httptest.NewRecorder()
		mux.ServeHTTP(rec, req)
		return rec
	}

	rec := call("127.0.0.1:5000", "Bearer "+tok)
	if rec.Code != http.StatusOK {
		t.Fatalf("live token from loopback: %d %s", rec.Code, rec.Body)
	}
	var got tools.RunReceipt
	if err := json.Unmarshal(rec.Body.Bytes(), &got); err != nil || !got.HumanReply || got.CurrentMessage != "cứ làm tiếp" {
		t.Fatalf("receipt = %+v, err %v", got, err)
	}
	if code := call("172.18.0.1:5000", "Bearer "+tok).Code; code != http.StatusForbidden {
		t.Fatalf("non-loopback caller: %d", code)
	}
	if code := call("127.0.0.1:5000", "Bearer deadbeef").Code; code != http.StatusNotFound {
		t.Fatalf("unknown token: %d", code)
	}
	if code := call("127.0.0.1:5000", "").Code; code != http.StatusUnauthorized {
		t.Fatalf("no token: %d", code)
	}
}
