package agent

import (
	"context"
	"testing"

	"github.com/nextlevelbuilder/goclaw/internal/providers"
	"github.com/nextlevelbuilder/goclaw/internal/tools"
)

// Matches production: runViaPipeline builds the bridge state as &runState{},
// and the loop detector initialises its maps lazily.
func endRunState() *runState {
	return &runState{}
}

// A tool that declares the run finished must stop the loop immediately, not
// after the loop detector's generic thresholds — those only fire once the same
// result has come back several times, which is several wasted LLM round trips
// too late for a guard whose answer is already determined.
func TestProcessToolResult_EndRunBreaksImmediately(t *testing.T) {
	col := &eventCollector{}
	loop := &Loop{id: "cf-designer", onEvent: col.onEvent}
	rs := endRunState()
	req := &RunRequest{RunID: "run-1", SessionKey: "s"}
	res := tools.SilentResult("DESIGN_STATUS: COMPLETE\nIMAGE_COUNT: 1\nIMAGE_PATH: MEDIA:/w/a.png")
	res.EndRun = true

	_, warnings, action := loop.processToolResult(context.Background(), rs, req, col.onEvent,
		providers.ToolCall{ID: "1", Name: "create_image"}, "create_image", res, false)

	if action != toolResultBreak {
		t.Fatalf("action = %v, want toolResultBreak on the first EndRun result", action)
	}
	if len(warnings) != 0 {
		t.Errorf("warnings = %v, want none", warnings)
	}
	// The tool supplies the answer; without this the run ends empty.
	if rs.finalContent != res.ForLLM {
		t.Errorf("finalContent = %q, want the tool's result", rs.finalContent)
	}
	if !rs.endRunRequested {
		t.Error("endRunRequested not set; syncBridgeToState would drop finalContent")
	}
	// Ending deliberately is not a loop kill. LoopKilled auto-fails team tasks,
	// so reusing it here would fail a delegation whose work actually succeeded.
	if rs.loopKilled {
		t.Error("loopKilled set; a deliberate end must not report the run as failed")
	}
}

func TestProcessToolResult_WithoutEndRunContinues(t *testing.T) {
	col := &eventCollector{}
	loop := &Loop{id: "cf-designer", onEvent: col.onEvent}
	rs := endRunState()
	req := &RunRequest{RunID: "run-1", SessionKey: "s"}

	_, _, action := loop.processToolResult(context.Background(), rs, req, col.onEvent,
		providers.ToolCall{ID: "1", Name: "create_image"}, "create_image",
		tools.SilentResult("MEDIA:/w/a.png"), false)

	if action != toolResultContinue {
		t.Fatalf("action = %v, want toolResultContinue for an ordinary result", action)
	}
	if rs.endRunRequested || rs.finalContent != "" {
		t.Error("ordinary result must not end the run")
	}
}
