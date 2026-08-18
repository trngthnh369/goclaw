package tools

import (
	"context"
	"sync"
	"testing"
	"time"

	"github.com/nextlevelbuilder/goclaw/internal/bus"
)

// outboundRecorder stands in for the channel dispatcher in tests: it drains the
// outbound queue, reports every message as delivered, and keeps what it saw so a
// test can still assert on it.
//
// Both halves are needed. The media send path publishes with a Result channel
// and blocks up to mediaSendTimeout for a verdict, so a test that wires a bus
// but runs no dispatcher stalls for the full timeout and then fails with "not
// confirmed" — draining is what unblocks it. But a plain background drain would
// swallow the messages a test wants to count, so the recorder keeps them.
type outboundRecorder struct {
	mu   sync.Mutex
	msgs []bus.OutboundMessage
}

// recordOutbound starts the stand-in dispatcher for mb; it stops with the test.
func recordOutbound(t *testing.T, mb *bus.MessageBus) *outboundRecorder {
	t.Helper()

	rec := &outboundRecorder{}
	ctx, cancel := context.WithCancel(context.Background())
	t.Cleanup(cancel)

	go func() {
		for {
			msg, ok := mb.SubscribeOutbound(ctx)
			if !ok {
				return
			}
			rec.mu.Lock()
			rec.msgs = append(rec.msgs, msg)
			rec.mu.Unlock()
			// Deliver last: a media send unblocks on this, so recording first
			// guarantees the message is visible once Execute returns.
			msg.Deliver(nil)
		}
	}()

	return rec
}

// take returns the recorded messages, waiting briefly for want of them.
//
// Text sends do not wait for a delivery verdict, so they can still be in flight
// when Execute returns; polling mirrors what the previous inline drain helper
// did with its short per-read timeout.
func (r *outboundRecorder) take(want int) []bus.OutboundMessage {
	deadline := time.Now().Add(2 * time.Second)
	for {
		r.mu.Lock()
		got := len(r.msgs)
		r.mu.Unlock()
		if got >= want || time.Now().After(deadline) {
			break
		}
		time.Sleep(time.Millisecond)
	}
	// Settle briefly so an unexpected extra publish is caught rather than raced past.
	time.Sleep(10 * time.Millisecond)

	r.mu.Lock()
	defer r.mu.Unlock()
	out := make([]bus.OutboundMessage, len(r.msgs))
	copy(out, r.msgs)
	r.msgs = nil
	return out
}
