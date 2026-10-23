package providers

import (
	"fmt"
	"io"
	"sync"
	"sync/atomic"
	"time"
)

// errStreamIdle reports a stream that went quiet for longer than the idle
// limit. It is a net.Error with Timeout() true, so failover classifies it as a
// timeout like any other network stall.
type errStreamIdle struct{ idle time.Duration }

func (e errStreamIdle) Error() string {
	return fmt.Sprintf("stream idle timeout: no data for %s", e.idle)
}
func (errStreamIdle) Timeout() bool   { return true }
func (errStreamIdle) Temporary() bool { return true }

// idleTimeoutBody closes the wrapped body when no Read returns data for idle.
// A Read blocked on a silent upstream then fails with errStreamIdle instead of
// hanging until the caller's context is cancelled.
type idleTimeoutBody struct {
	body    io.ReadCloser
	idle    time.Duration
	timer   *time.Timer
	expired atomic.Bool
	once    sync.Once
}

func newIdleTimeoutBody(body io.ReadCloser, idle time.Duration) io.ReadCloser {
	if idle <= 0 {
		return body
	}
	b := &idleTimeoutBody{body: body, idle: idle}
	b.timer = time.AfterFunc(idle, func() {
		b.expired.Store(true)
		_ = b.body.Close()
	})
	return b
}

func (b *idleTimeoutBody) Read(p []byte) (int, error) {
	n, err := b.body.Read(p)
	if b.expired.Load() {
		return n, errStreamIdle{idle: b.idle}
	}
	if n > 0 {
		b.timer.Reset(b.idle)
	}
	return n, err
}

func (b *idleTimeoutBody) Close() error {
	var err error
	b.once.Do(func() {
		b.timer.Stop()
		if !b.expired.Load() {
			err = b.body.Close()
		}
	})
	return err
}
