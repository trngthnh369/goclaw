//go:build linux

package cmd

import (
	"os/exec"
	"strings"
	"testing"

	"golang.org/x/sys/unix"
)

func TestHardenGatewayProcess_ChildCannotReadParentEnviron(t *testing.T) {
	hardenGatewayProcess()

	dumpable, err := unix.PrctlRetInt(unix.PR_GET_DUMPABLE, 0, 0, 0, 0)
	if err != nil {
		t.Fatalf("PR_GET_DUMPABLE: %v", err)
	}
	if dumpable != 0 {
		t.Fatalf("dumpable = %d, want 0", dumpable)
	}

	// The same bypass that defeated the shell deny regexes in production.
	out, err := exec.Command("sh", "-c", `p=/proc/$PPID; cat $p/envir''on`).CombinedOutput()
	if err == nil {
		t.Fatalf("child read the parent's environ (%d bytes); want permission denied", len(out))
	}
	if !strings.Contains(strings.ToLower(string(out)), "permission denied") {
		t.Fatalf("unexpected failure reading parent environ: %s", out)
	}
}
