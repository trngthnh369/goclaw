//go:build linux

package tools

import (
	"bytes"
	"context"
	"crypto/hmac"
	"crypto/rand"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"io/fs"
	"log/slog"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"time"

	"golang.org/x/sys/unix"

	"github.com/nextlevelbuilder/goclaw/internal/bus"
)

// A Video Factory review draft in master mode: the agent names only the master
// (reels_master); the gateway snapshots it, derives the Discord review cut from
// those exact bytes and signs a record binding the two. The person then watches
// a cut the gateway made from the file it will publish - a sha line alone would
// only prove which two files the agent supplied (plan 260928-q7m2, Phase 3).
//
// The director's exec runs as the gateway's uid, so every file here is writable
// by it. Integrity therefore rests on: the record's HMAC (key only in this
// process's memory; the gateway is non-dumpable), a re-hash of the master at
// approval, and the upload-stream hash before Facebook's finish call.

const (
	reelsDraftsDir      = ".goclaw/reels-drafts"
	reelsDraftsIndexDir = "by-review"
	reelsDraftSlack     = int64(12 << 20)  // review cut + record, reserved with the master
	reviewCutMaxBytes   = int64(9_500_000) // Discord's 10 MB upload cap without boost
	reviewProbeTimeout  = 30 * time.Second
	reviewAudioKbps     = 96
	reviewStderrCap     = 64 << 10
	reelsMinSeconds     = 3.0
	reelsMaxSeconds     = 90.0
)

// mediaToolDir holds root-owned, execute-only copies of ffmpeg and ffprobe,
// installed by docker-entrypoint.sh. A process that execs an unreadable binary
// is non-dumpable, so the agent's exec cannot open its /proc/<pid>/fd.
var mediaToolDir = "/usr/libexec/goclaw"

// reelsDraftsMaxBytes bounds the draft store (about six 90 s masters). A
// variable, like mediaTools, so tests can shrink or stub it.
var reelsDraftsMaxBytes = int64(600 << 20)

var mediaTools = trustedMediaTools

// gatewayNonDumpable reports whether this process is non-dumpable. The draft
// key lives in its memory, so a dumpable gateway (hardening failed at start)
// must not create drafts. A variable so tests, which run dumpable, can stub it.
var gatewayNonDumpable = func() bool {
	v, err := unix.PrctlRetInt(unix.PR_GET_DUMPABLE, 0, 0, 0, 0)
	return err == nil && v == 0
}

// reviewCutTimeout bounds one derivation (Step 0: 191 s for 90 s at the p90 slowdown).
var reviewCutTimeout = 240 * time.Second

// Derivations run one at a time per tenant, and at most reelsDeriveMax across
// the gateway; a send that finds no slot is refused at once instead of silently
// spending its caller's time. Per tenant, so one tenant cannot starve another.
const reelsDeriveMax = 2

var (
	reelsDeriveGlobal = make(chan struct{}, reelsDeriveMax)
	reelsDeriveSlots  sync.Map // tenant -> chan struct{} (capacity 1)
)

func reelsDeriveSlot(tenant string) chan struct{} {
	slot, _ := reelsDeriveSlots.LoadOrStore(tenant, make(chan struct{}, 1))
	return slot.(chan struct{})
}

// acquireReelsDerive takes the tenant's slot and a gateway-wide one.
func acquireReelsDerive(tenant string) (release func(), ok bool) {
	slot := reelsDeriveSlot(tenant)
	select {
	case slot <- struct{}{}:
	default:
		return nil, false
	}
	select {
	case reelsDeriveGlobal <- struct{}{}:
	default:
		<-slot
		return nil, false
	}
	return func() { <-reelsDeriveGlobal; <-slot }, true
}

// reviewCutSizes is the gateway's review cut: always 720p, which measured
// 0.71 s per video-second (superfast, 2 threads) against 2.1 s at 1080p.
var reviewCutSizes = map[[2]int][2]int{
	{1080, 1920}: {720, 1280},
	{1920, 1080}: {1280, 720},
	{1080, 1080}: {720, 720},
}

type reelsMasterProbe struct {
	width, height int
	duration      float64
}

// sendReelsMasterDraft sends a master-mode Reels review draft. Every failure
// returns before PublishOutbound and removes the partial draft directory.
func (t *MessageTool) sendReelsMasterDraft(ctx context.Context, channel, target, message, rawPath string) *Result {
	caption, err := reelsCaption(message)
	if err != nil {
		return ErrorResult(err.Error())
	}
	if embeddedMediaPattern.MatchString(message) {
		return ErrorResult("reels_master replaces MEDIA: - the gateway attaches the review video itself; remove the MEDIA: line")
	}
	if t.msgBus == nil {
		return ErrorResult("no message bus available for a review draft")
	}
	// Only a chat whose channel instance lists it as a Reels review chat can
	// approve a master-mode draft, so no other destination is worth a derivation.
	if t.reelsReviewChat == nil || !t.reelsReviewChat(channel, target) {
		slog.Warn("security.reels_master_target_rejected", "channel", channel, "target", target)
		return ErrorResult("reels_master can only be sent to a channel configured as a Reels review channel")
	}
	release, ok := acquireReelsDerive(reelsTenant(ctx))
	if !ok {
		return ErrorResult("another review video is being prepared; wait a minute and send again")
	}
	defer release()
	if !gatewayNonDumpable() {
		slog.Warn("security.reels_master_gateway_dumpable")
		return ErrorResult("the gateway is not hardened, so it cannot sign a review draft")
	}
	ffmpeg, ffprobe, err := mediaTools()
	if err != nil {
		slog.Warn("security.reels_master_tools_untrusted", "error", err.Error())
		return ErrorResult("ffmpeg is not available to the gateway, so the master cannot be reviewed")
	}
	source, size, err := openReelsMaster(ctx, rawPath)
	if err != nil {
		slog.Warn("security.reels_master_rejected", "reason", err.Error())
		return ErrorResult("reels_master rejected: " + err.Error())
	}
	defer source.Close()

	draftsRoot := t.reelsDraftsRoot(ctx)
	if draftsRoot == "" {
		return ErrorResult("gateway data directory unavailable for the review draft")
	}
	if err := os.MkdirAll(filepath.Join(draftsRoot, reelsDraftsIndexDir), 0o700); err != nil {
		return ErrorResult("cannot create the review draft store")
	}
	sweepReelsDrafts(draftsRoot, time.Now())
	if used := reelsDraftsUsage(draftsRoot); used+size+reelsDraftSlack > reelsDraftsMaxBytes {
		return ErrorResult(fmt.Sprintf("the review draft store is full (%d MB of %d MB); approve or discard older drafts first",
			used>>20, reelsDraftsMaxBytes>>20))
	}

	nonceBytes := make([]byte, 16)
	if _, err := rand.Read(nonceBytes); err != nil {
		return ErrorResult("cannot create a draft id")
	}
	nonce := hex.EncodeToString(nonceBytes)
	dir := filepath.Join(draftsRoot, nonce)
	if err := os.Mkdir(dir, 0o700); err != nil {
		return ErrorResult("cannot create the review draft directory")
	}
	sent := false
	defer func() {
		if !sent {
			_ = os.RemoveAll(dir)
		}
	}()

	// One unnamed copy feeds the probe, the derivation and the named master, so
	// the agent cannot change its file between those steps; the copy has no path
	// and only non-dumpable processes hold it.
	work, err := openUnnamed(dir)
	if err != nil {
		return ErrorResult("cannot create the working copy of the master")
	}
	defer work.Close()
	masterSHA, n, err := copyHashed(work, source, MaxReelUploadBytes)
	if err != nil || n != size {
		return ErrorResult("could not read the whole master")
	}
	probe, err := probeReelsMaster(ctx, ffprobe, work)
	if err != nil {
		slog.Warn("security.reels_master_profile_rejected", "reason", err.Error())
		return ErrorResult("reels_master rejected: " + err.Error())
	}
	masterPath := filepath.Join(dir, "master.mp4")
	if err := writeNamedCopy(work, masterPath, masterSHA); err != nil {
		return ErrorResult("could not store the master snapshot")
	}
	reviewPath := filepath.Join(dir, "review.mp4")
	if err := deriveReviewCut(ctx, ffmpeg, work, reviewPath, probe, nonce); err != nil {
		slog.Warn("message.reels_review_cut_failed", "error", err.Error())
		return ErrorResult("the gateway could not make the review video: " + err.Error())
	}
	reviewSHA, err := hashFeedPostFile(reviewPath)
	if err != nil {
		return ErrorResult("could not hash the review video")
	}

	rec := reelsDraftRecord{
		V: 1, AgentKey: ToolAgentKeyFromCtx(ctx), Channel: channel, ChatID: target, Nonce: nonce,
		CaptionSHA256: sha256Hex(normalizeApprovalContent(caption)), ReviewSHA256: reviewSHA,
		MasterSHA256: masterSHA, MasterBytes: size, CreatedAt: time.Now().UTC(),
		TenantID: reelsTenant(ctx),
	}
	rec.HMAC = rec.mac()
	body, _ := json.MarshalIndent(rec, "", "  ")
	if err := writeFileAtomic(filepath.Join(dir, "record.json"), body); err != nil {
		return ErrorResult("could not store the review draft record")
	}
	if err := writeFileAtomic(filepath.Join(draftsRoot, reelsDraftsIndexDir, reviewSHA), []byte(nonce)); err != nil {
		return ErrorResult("could not index the review draft")
	}

	outMsg := bus.OutboundMessage{
		Channel: channel,
		ChatID:  target,
		Content: strings.TrimRight(message, "\n") + "\n" + reelsMasterLine + " " + masterSHA,
		Media:   []bus.MediaAttachment{{URL: reviewPath, ContentType: "video/mp4"}},
		// The 📝 marker makes a ✅ or a bare "duyệt" approve this draft.
		Metadata: map[string]string{MetaContentFactoryReviewDraft: "true"},
	}
	if isGroupContext(ctx) {
		outMsg.Metadata["group_id"] = target
	}
	t.msgBus.PublishOutbound(outMsg)
	sent = true
	slog.Info("message.reels_master_draft_sent", "channel", channel, "nonce", nonce,
		"master_sha256", masterSHA, "review_sha256", reviewSHA, "duration", probe.duration)
	return SilentResult(fmt.Sprintf(`{"status":"sent","channel":"%s","target":"%s","master_sha256":"%s"}`,
		channel, target, masterSHA))
}

// loadReelsDraft returns the signed record and directory of the master-mode
// draft whose review cut has the approved digest.
func (t *MessageTool) loadReelsDraft(ctx context.Context, approvedSHA string) (reelsDraftRecord, string, error) {
	var rec reelsDraftRecord
	draftsRoot := t.reelsDraftsRoot(ctx)
	if draftsRoot == "" {
		return rec, "", errors.New("gateway data directory unavailable")
	}
	nonce, err := os.ReadFile(filepath.Join(draftsRoot, reelsDraftsIndexDir, approvedSHA))
	if err != nil {
		return rec, "", errors.New("no draft record")
	}
	name := strings.TrimSpace(string(nonce))
	if _, err := hex.DecodeString(name); err != nil || len(name) != 32 {
		return rec, "", errors.New("malformed draft index")
	}
	dir := filepath.Join(draftsRoot, name)
	body, err := os.ReadFile(filepath.Join(dir, "record.json"))
	if err != nil || json.Unmarshal(body, &rec) != nil {
		return rec, "", errors.New("unreadable draft record")
	}
	// The index is only a hint: the signed record must name this review cut.
	if !hmac.Equal([]byte(rec.HMAC), []byte(rec.mac())) {
		return rec, "", errors.New("draft record signature invalid")
	}
	if rec.ReviewSHA256 != approvedSHA || rec.Nonce != name {
		return rec, "", errors.New("draft record does not match the approved video")
	}
	return rec, dir, nil
}

// trustedMediaTools returns the execute-only ffmpeg and ffprobe, refusing any
// copy the gateway's own uid could read or replace.
func trustedMediaTools() (ffmpeg, ffprobe string, err error) {
	if err := checkRootOwned(mediaToolDir, true); err != nil {
		return "", "", err
	}
	ffmpeg, ffprobe = filepath.Join(mediaToolDir, "ffmpeg"), filepath.Join(mediaToolDir, "ffprobe")
	for _, p := range []string{ffmpeg, ffprobe} {
		if err := checkRootOwned(p, false); err != nil {
			return "", "", err
		}
	}
	return ffmpeg, ffprobe, nil
}

func checkRootOwned(path string, dir bool) error {
	info, err := os.Lstat(path)
	if err != nil {
		return fmt.Errorf("%s: %w", path, err)
	}
	st, ok := info.Sys().(*syscall.Stat_t)
	if !ok || st.Uid != 0 || info.Mode()&fs.ModeSymlink != 0 || info.Mode().Perm()&0o022 != 0 {
		return fmt.Errorf("%s is not a root-owned, non-writable file", path)
	}
	if dir {
		if !info.IsDir() {
			return fmt.Errorf("%s is not a directory", path)
		}
		return nil
	}
	// Unreadable to everyone makes the exec'ed process non-dumpable.
	if !info.Mode().IsRegular() || info.Mode().Perm()&0o444 != 0 || info.Mode().Perm()&0o111 == 0 {
		return fmt.Errorf("%s is not an execute-only binary", path)
	}
	return nil
}

// openReelsMaster opens the agent's master inside its own workspace, whatever
// the restrict flag says: no path component may resolve outside the workspace,
// the file must be a regular file with a single link, within the Reels cap.
func openReelsMaster(ctx context.Context, rawPath string) (*os.File, int64, error) {
	workspace := ToolWorkspaceFromCtx(ctx)
	if workspace == "" || rawPath == "" {
		return nil, 0, errors.New("no workspace to read the master from")
	}
	wsAbs, err := filepath.Abs(workspace)
	if err != nil {
		return nil, 0, errors.New("invalid workspace")
	}
	p := strings.TrimPrefix(strings.TrimSpace(rawPath), "MEDIA:")
	if !filepath.IsAbs(p) {
		p = filepath.Join(wsAbs, p)
	}
	rel, err := filepath.Rel(wsAbs, filepath.Clean(p))
	if err != nil || rel == "." || rel == ".." || strings.HasPrefix(rel, ".."+string(filepath.Separator)) {
		return nil, 0, errors.New("the master must be inside your workspace")
	}
	if !strings.EqualFold(filepath.Ext(rel), ".mp4") {
		return nil, 0, errors.New("the master must be an .mp4 file")
	}
	root, err := os.OpenRoot(wsAbs)
	if err != nil {
		return nil, 0, errors.New("cannot open the workspace")
	}
	defer root.Close()
	leaf, err := root.Lstat(rel)
	if err != nil || leaf.Mode()&fs.ModeSymlink != 0 || !leaf.Mode().IsRegular() {
		return nil, 0, errors.New("the master is not a regular file")
	}
	f, err := root.Open(rel) // refuses any component that resolves outside the root
	if err != nil {
		return nil, 0, errors.New("cannot open the master inside the workspace")
	}
	info, err := f.Stat()
	st, ok := info.Sys().(*syscall.Stat_t)
	switch {
	case err != nil || !info.Mode().IsRegular() || !os.SameFile(leaf, info):
		err = errors.New("the master changed while it was opened")
	case !ok || st.Nlink != 1:
		err = errors.New("the master has more than one hard link")
	case info.Size() <= 0 || info.Size() > MaxReelUploadBytes:
		err = fmt.Errorf("the master is %d bytes; the Reels limit is %d", info.Size(), MaxReelUploadBytes)
	}
	if err != nil {
		f.Close()
		return nil, 0, err
	}
	return f, info.Size(), nil
}

// probeReelsMaster checks the master's media profile on the working copy before
// any decode: an MP4 box structure, one H.264 video stream at a supported size,
// at most one AAC audio stream, nothing else, 3-90 s.
func probeReelsMaster(ctx context.Context, ffprobe string, work *os.File) (reelsMasterProbe, error) {
	var p reelsMasterProbe
	head := make([]byte, 8)
	if _, err := work.ReadAt(head, 0); err != nil || string(head[4:8]) != "ftyp" {
		return p, errors.New("the master is not an MP4 file")
	}
	pctx, cancel := context.WithTimeout(ctx, reviewProbeTimeout)
	defer cancel()
	out, err := runMediaTool(pctx, ffprobe, work, 1<<20, "-v", "error", "-protocol_whitelist", "fd",
		"-enable_drefs", "0", "-f", "mov", "-fd", "3", "-show_entries", "stream=codec_type,codec_name,width,height,avg_frame_rate:format=duration",
		"-of", "json", "fd:")
	if err != nil {
		return p, errors.New("the master could not be read as a video")
	}
	var doc struct {
		Streams []struct {
			CodecType string `json:"codec_type"`
			CodecName string `json:"codec_name"`
			Width     int    `json:"width"`
			Height    int    `json:"height"`
			FrameRate string `json:"avg_frame_rate"`
		} `json:"streams"`
		Format struct {
			Duration string `json:"duration"`
		} `json:"format"`
	}
	if err := json.Unmarshal(out, &doc); err != nil {
		return p, errors.New("the master's probe output was unreadable")
	}
	video, audio := 0, 0
	for _, s := range doc.Streams {
		switch {
		case s.CodecType == "video" && s.CodecName == "h264":
			video++
			p.width, p.height = s.Width, s.Height
			if fps := frameRate(s.FrameRate); fps <= 0 || fps > 60 {
				return p, fmt.Errorf("the master's frame rate %q is not supported", s.FrameRate)
			}
		case s.CodecType == "audio" && s.CodecName == "aac":
			audio++
		default:
			return p, fmt.Errorf("the master has an unsupported %s stream (%s)", s.CodecType, s.CodecName)
		}
	}
	if video != 1 || audio > 1 {
		return p, fmt.Errorf("the master must have one H.264 video and at most one AAC audio stream (has %d and %d)", video, audio)
	}
	if _, ok := reviewCutSizes[[2]int{p.width, p.height}]; !ok {
		return p, fmt.Errorf("the master's size %dx%d is not a Video Factory format", p.width, p.height)
	}
	p.duration, err = strconv.ParseFloat(doc.Format.Duration, 64)
	if err != nil || p.duration < reelsMinSeconds || p.duration > reelsMaxSeconds {
		return p, fmt.Errorf("the master lasts %q s; Reels need %.0f-%.0f s", doc.Format.Duration, reelsMinSeconds, reelsMaxSeconds)
	}
	return p, nil
}

// deriveReviewCut encodes the review cut from the working copy: 720p, sized to
// fit Discord's cap, with the nonce in its metadata so every draft's cut (and so
// its digest) is unique even for the same master.
func deriveReviewCut(ctx context.Context, ffmpeg string, work *os.File, outPath string, p reelsMasterProbe, nonce string) error {
	size := reviewCutSizes[[2]int{p.width, p.height}]
	kbps := int(float64(reviewCutMaxBytes)*8/1000/p.duration*0.94) - reviewAudioKbps
	for attempt := 0; attempt < 2; attempt++ {
		if kbps < 200 {
			return errors.New("the master is too long for a review cut under 9.5 MB")
		}
		dctx, cancel := context.WithTimeout(ctx, reviewCutTimeout)
		_, err := runMediaTool(dctx, ffmpeg, work, 0, "-nostdin", "-y", "-v", "error",
			// fd only, drefs off: nothing in the agent's container may make
			// the demuxer open another file. The output is a plain path.
			"-protocol_whitelist", "fd", "-enable_drefs", "0", "-f", "mov", "-fd", "3", "-i", "fd:",
			"-map", "0:v:0", "-map", "0:a:0?", "-map_metadata", "-1", "-metadata", "comment="+nonce,
			"-vf", fmt.Sprintf("scale=%d:%d", size[0], size[1]),
			"-c:v", "libx264", "-preset", "superfast", "-threads", "2",
			"-b:v", fmt.Sprintf("%dk", kbps), "-maxrate", fmt.Sprintf("%dk", kbps), "-bufsize", fmt.Sprintf("%dk", kbps*2),
			"-pix_fmt", "yuv420p", "-g", "60", "-c:a", "aac", "-b:a", fmt.Sprintf("%dk", reviewAudioKbps),
			"-movflags", "+faststart", outPath)
		cancel()
		if err != nil {
			return err
		}
		info, err := os.Stat(outPath)
		if err != nil {
			return err
		}
		if info.Size() <= reviewCutMaxBytes {
			return nil
		}
		kbps = kbps * 8 / 10
	}
	return errors.New("the review cut stayed over 9.5 MB")
}

// runMediaTool runs a trusted media tool with an empty environment, the working
// copy as fd 3, a context deadline (the process is killed on expiry) and capped
// output. stdoutCap 0 discards stdout.
func runMediaTool(ctx context.Context, bin string, work *os.File, stdoutCap int64, args ...string) ([]byte, error) {
	if _, err := work.Seek(0, io.SeekStart); err != nil {
		return nil, err
	}
	cmd := exec.CommandContext(ctx, bin, args...)
	cmd.Env = []string{}
	cmd.ExtraFiles = []*os.File{work}
	cmd.Stdin = nil
	var stdout, stderr cappedBuffer
	stdout.max, stderr.max = stdoutCap, reviewStderrCap
	cmd.Stdout, cmd.Stderr = &stdout, &stderr
	if err := cmd.Run(); err != nil {
		if ctx.Err() != nil {
			return nil, errors.New("timed out")
		}
		// The tool's stderr describes the agent's file; it goes to the log only.
		slog.Warn("message.reels_media_tool_failed", "tool", filepath.Base(bin), "error", err.Error(),
			"stderr", strings.TrimSpace(truncateBytes(stderr.Bytes(), 300)))
		return nil, fmt.Errorf("%s failed", filepath.Base(bin))
	}
	return stdout.Bytes(), nil
}

type cappedBuffer struct {
	bytes.Buffer
	max int64
}

func (b *cappedBuffer) Write(p []byte) (int, error) {
	if room := b.max - int64(b.Len()); room > 0 {
		if int64(len(p)) > room {
			b.Buffer.Write(p[:room])
		} else {
			b.Buffer.Write(p)
		}
	}
	return len(p), nil
}

func copyHashed(dst io.Writer, src io.Reader, limit int64) (string, int64, error) {
	h := sha256.New()
	n, err := io.Copy(io.MultiWriter(dst, h), io.LimitReader(src, limit+1))
	if err != nil {
		return "", n, err
	}
	if n > limit {
		return "", n, errors.New("over the size limit")
	}
	return hex.EncodeToString(h.Sum(nil)), n, nil
}

// writeNamedCopy stores the working copy as the named snapshot published at
// approval, checking its digest while writing.
func writeNamedCopy(work *os.File, path, wantSHA string) error {
	if _, err := work.Seek(0, io.SeekStart); err != nil {
		return err
	}
	tmp, err := os.CreateTemp(filepath.Dir(path), ".master-*")
	if err != nil {
		return err
	}
	defer os.Remove(tmp.Name())
	gotSHA, _, err := copyHashed(tmp, work, MaxReelUploadBytes)
	if err == nil && gotSHA != wantSHA {
		err = errors.New("digest changed while copying")
	}
	if err == nil {
		err = tmp.Sync()
	}
	if cerr := tmp.Close(); err == nil {
		err = cerr
	}
	if err != nil {
		return err
	}
	return os.Rename(tmp.Name(), path)
}

func writeFileAtomic(path string, body []byte) error {
	tmp, err := os.CreateTemp(filepath.Dir(path), ".tmp-*")
	if err != nil {
		return err
	}
	defer os.Remove(tmp.Name())
	if err := tmp.Chmod(0o600); err != nil {
		tmp.Close()
		return err
	}
	if _, err := tmp.Write(body); err != nil {
		tmp.Close()
		return err
	}
	if err := tmp.Sync(); err != nil {
		tmp.Close()
		return err
	}
	if err := tmp.Close(); err != nil {
		return err
	}
	return os.Rename(tmp.Name(), path)
}

// sweepReelsDrafts removes drafts older than reelsDraftMaxAge and index entries
// whose draft is gone. The caller holds the derivation slot, so no send is
// writing a draft meanwhile.
func sweepReelsDrafts(draftsRoot string, now time.Time) {
	entries, err := os.ReadDir(draftsRoot)
	if err != nil {
		return
	}
	for _, e := range entries {
		if !e.IsDir() || e.Name() == reelsDraftsIndexDir {
			continue
		}
		if info, err := e.Info(); err == nil && now.Sub(info.ModTime()) > reelsDraftMaxAge {
			_ = os.RemoveAll(filepath.Join(draftsRoot, e.Name()))
		}
	}
	index := filepath.Join(draftsRoot, reelsDraftsIndexDir)
	links, _ := os.ReadDir(index)
	for _, l := range links {
		nonce, err := os.ReadFile(filepath.Join(index, l.Name()))
		if err != nil {
			continue
		}
		if _, err := os.Stat(filepath.Join(draftsRoot, strings.TrimSpace(string(nonce)))); errors.Is(err, fs.ErrNotExist) {
			_ = os.Remove(filepath.Join(index, l.Name()))
		}
	}
}

func reelsDraftsUsage(draftsRoot string) int64 {
	var total int64
	_ = filepath.WalkDir(draftsRoot, func(_ string, d fs.DirEntry, err error) error {
		if err == nil && d.Type().IsRegular() {
			if info, ierr := d.Info(); ierr == nil {
				total += info.Size()
			}
		}
		return nil
	})
	return total
}

// removeReelsDraft deletes a published draft and its index entry.
func (t *MessageTool) removeReelsDraft(ctx context.Context, dir, reviewSHA string) {
	_ = os.RemoveAll(dir)
	if draftsRoot := t.reelsDraftsRoot(ctx); draftsRoot != "" {
		_ = os.Remove(filepath.Join(draftsRoot, reelsDraftsIndexDir, reviewSHA))
	}
}

// reelsDraftsRoot is the tenant's own draft store: its size cap, sweep and
// index never see another tenant's drafts.
func (t *MessageTool) reelsDraftsRoot(ctx context.Context) string {
	root := t.feedPostStateRoot(ctx)
	if root == "" {
		return ""
	}
	return filepath.Join(root, filepath.FromSlash(reelsDraftsDir), reelsTenant(ctx))
}

func frameRate(s string) float64 {
	num, den, found := strings.Cut(s, "/")
	n, err := strconv.ParseFloat(num, 64)
	if err != nil {
		return 0
	}
	if !found {
		return n
	}
	d, err := strconv.ParseFloat(den, 64)
	if err != nil || d == 0 {
		return 0
	}
	return n / d
}

// openUnnamed returns a read-write file with no name in dir: O_TMPFILE where the
// filesystem supports it, else a fresh temp file unlinked at once (overlayfs
// refuses O_TMPFILE), leaving only this process's descriptor.
func openUnnamed(dir string) (*os.File, error) {
	if f, err := os.OpenFile(dir, os.O_RDWR|unix.O_TMPFILE, 0o600); err == nil {
		return f, nil
	}
	f, err := os.CreateTemp(dir, ".work-*")
	if err != nil {
		return nil, err
	}
	if err := os.Remove(f.Name()); err != nil {
		f.Close()
		return nil, err
	}
	return f, nil
}
