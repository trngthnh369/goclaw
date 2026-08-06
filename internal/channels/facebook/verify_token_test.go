package facebook

import (
	"context"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

// meServer stands in for Graph's GET /me, which answers with whoever owns the
// token rather than with the page you meant to use.
func meServer(t *testing.T, body string) *httptest.Server {
	t.Helper()
	return httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(body))
	}))
}

func TestVerifyToken_RejectsTokenForAnotherIdentity(t *testing.T) {
	// A Business-Manager system-user token: /me names the system user, not the
	// page. This shipped as "verified" and hid a broken credential for three days.
	srv := meServer(t, `{"id":"122095042593422510","name":"content_factory"}`)
	defer srv.Close()

	old := graphAPIBase
	graphAPIBase = srv.URL
	defer func() { graphAPIBase = old }()

	g := NewGraphClient("tok-fixture", "1193343723865442")

	err := g.VerifyToken(context.Background())
	if err == nil {
		t.Fatal("VerifyToken = nil for a token belonging to a different identity, want error")
	}
	// The operator needs both ids to act on this, plus the way out.
	for _, want := range []string{"122095042593422510", "1193343723865442", "fields=access_token"} {
		if !strings.Contains(err.Error(), want) {
			t.Errorf("error %q does not mention %q", err.Error(), want)
		}
	}
}

func TestVerifyToken_AcceptsMatchingPageToken(t *testing.T) {
	srv := meServer(t, `{"id":"1193343723865442","name":"AI Insights Vietnam"}`)
	defer srv.Close()

	old := graphAPIBase
	graphAPIBase = srv.URL
	defer func() { graphAPIBase = old }()

	g := NewGraphClient("tok-fixture", "1193343723865442")

	if err := g.VerifyToken(context.Background()); err != nil {
		t.Fatalf("VerifyToken = %v for a matching page token, want nil", err)
	}
}

// The detail accessors are what let callers outside this package log why a
// request failed without reaching for err.Error() on arbitrary error types.
func TestGraphAPIError_DetailAccessors(t *testing.T) {
	err := &graphAPIError{code: 210, msg: "(#210) A page access token is required to request this resource."}
	if got := err.APIErrorCode(); got != 210 {
		t.Errorf("APIErrorCode = %d, want 210", got)
	}
	if !strings.Contains(err.APIErrorMessage(), "page access token is required") {
		t.Errorf("APIErrorMessage = %q", err.APIErrorMessage())
	}
}
