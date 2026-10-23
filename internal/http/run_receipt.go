package http

import (
	"encoding/json"
	"net"
	"net/http"
	"strings"

	"github.com/nextlevelbuilder/goclaw/internal/tools"
)

// RegisterRunReceiptRoute serves GET /v1/runs/receipt.
//
// A process started by `exec` presents the token the gateway gave its run
// (tools.RunReceiptEnv) and learns what the gateway knows about the turn that
// started the run, above all whether an allowlisted person was replying to the
// bot. The token is the only credential: it is unguessable, lives in memory and
// dies with the run. Loopback callers only, since exec runs next to the gateway.
func RegisterRunReceiptRoute(mux *http.ServeMux) {
	mux.HandleFunc("GET /v1/runs/receipt", handleRunReceipt)
}

func handleRunReceipt(w http.ResponseWriter, r *http.Request) {
	host, _, err := net.SplitHostPort(r.RemoteAddr)
	if ip := net.ParseIP(host); err != nil || ip == nil || !ip.IsLoopback() {
		http.Error(w, "loopback only", http.StatusForbidden)
		return
	}
	token, ok := strings.CutPrefix(r.Header.Get("Authorization"), "Bearer ")
	if !ok || strings.TrimSpace(token) == "" {
		http.Error(w, "missing run token", http.StatusUnauthorized)
		return
	}
	receipt, found := tools.LookupRunReceipt(strings.TrimSpace(token))
	if !found {
		http.Error(w, "unknown or expired run token", http.StatusNotFound)
		return
	}
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Cache-Control", "no-store")
	_ = json.NewEncoder(w).Encode(receipt)
}
