//go:build linux

package cmd

import (
	"log/slog"

	"golang.org/x/sys/unix"
)

// hardenGatewayProcess marks the gateway non-dumpable. Exec-tool children run
// under the gateway's uid, and for a dumpable process that is enough to read
// /proc/<gateway-pid>/environ (the encryption key, DB DSN, provider keys) no
// matter how the child's own env was scrubbed; the shell deny regexes only see
// command text and are bypassed by `p=/proc/$PPID; cat $p/envir''on`.
// Non-dumpable turns that /proc access into a CAP_SYS_PTRACE check. Children
// are unaffected: execve resets the flag.
func hardenGatewayProcess() {
	if err := unix.Prctl(unix.PR_SET_DUMPABLE, 0, 0, 0, 0); err != nil {
		slog.Warn("security.process_harden_failed", "error", err)
		return
	}
	slog.Info("security.process_non_dumpable")
}
