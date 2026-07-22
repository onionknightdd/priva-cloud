// priva-terminald is the non-Python WebSocket/PTY endpoint used only by the
// independent per-tenant Terminal pod. It intentionally has no data-spine or
// Kubernetes client and gives child shells a small allow-listed environment.
package main

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log"
	"net/http"
	"os"
	"os/exec"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"syscall"
	"time"
	"unicode/utf8"

	"github.com/coder/websocket"
	"github.com/creack/pty"
)

const (
	authHeader       = "X-Priva-Terminal-Authorized"
	maxInputMessage  = 64 * 1024
	ptyReadChunkSize = 4096
)

type config struct {
	listen      string
	maxSessions int
	idle        time.Duration
	lifetime    time.Duration
	outputRate  int
	outputBurst int
	outputBuf   int
	cwd         string
	shell       string
}

func envInt(key string, fallback int) int {
	v, err := strconv.Atoi(os.Getenv(key))
	if err != nil || v <= 0 {
		return fallback
	}
	return v
}

func loadConfig() config {
	listen := os.Getenv("PRIVA_TERMINAL_LISTEN")
	if listen == "" {
		listen = "0.0.0.0:8092"
	}
	cwd := os.Getenv("PRIVA_TERMINAL_CWD")
	if cwd == "" {
		cwd = "/workspace"
	}
	shell := os.Getenv("PRIVA_TERMINAL_SHELL")
	if shell == "" {
		shell = "/bin/bash"
	}
	return config{
		listen:      listen,
		maxSessions: envInt("PRIVA_TERMINAL_MAX_SESSIONS", 2),
		idle:        time.Duration(envInt("PRIVA_TERMINAL_IDLE_TIMEOUT_SECONDS", 1800)) * time.Second,
		lifetime:    time.Duration(envInt("PRIVA_TERMINAL_MAX_LIFETIME_SECONDS", 14400)) * time.Second,
		outputRate:  envInt("PRIVA_TERMINAL_OUTPUT_RATE", 256*1024),
		outputBurst: envInt("PRIVA_TERMINAL_OUTPUT_BURST", 1024*1024),
		outputBuf:   envInt("PRIVA_TERMINAL_OUTPUT_BUFFER", 1024*1024),
		cwd:         cwd,
		shell:       shell,
	}
}

type server struct {
	cfg          config
	mu           sync.Mutex
	sessions     map[string]struct{}
	revision     uint64
	drainUntil   time.Time
	lastActivity atomic.Int64
}

func newServer(cfg config) *server {
	s := &server{cfg: cfg, sessions: make(map[string]struct{})}
	s.touch()
	return s
}

func (s *server) touch() { s.lastActivity.Store(time.Now().Unix()) }

func (s *server) reserve() (string, bool) {
	s.mu.Lock()
	defer s.mu.Unlock()
	now := time.Now()
	if !s.drainUntil.IsZero() && now.Before(s.drainUntil) {
		return "", false
	}
	if !s.drainUntil.IsZero() {
		s.drainUntil = time.Time{}
	}
	if len(s.sessions) >= s.cfg.maxSessions {
		return "", false
	}
	b := make([]byte, 8)
	if _, err := rand.Read(b); err != nil {
		return "", false
	}
	id := hex.EncodeToString(b)
	s.sessions[id] = struct{}{}
	s.revision++
	s.touch()
	return id, true
}

func (s *server) release(id string) {
	s.mu.Lock()
	if _, ok := s.sessions[id]; ok {
		delete(s.sessions, id)
		s.revision++
	}
	s.mu.Unlock()
	s.touch()
}

func (s *server) state() (active int, revision uint64, draining bool) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if !s.drainUntil.IsZero() && !time.Now().Before(s.drainUntil) {
		s.drainUntil = time.Time{}
	}
	return len(s.sessions), s.revision, !s.drainUntil.IsZero()
}

func (s *server) health(w http.ResponseWriter, _ *http.Request) {
	active, revision, draining := s.state()
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(map[string]any{
		"status":           "ok",
		"active_sessions":  active,
		"last_activity_ts": s.lastActivity.Load(),
		"session_revision": revision,
		"draining":         draining,
	})
}

func (s *server) drain(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	expected, err := strconv.ParseUint(r.URL.Query().Get("revision"), 10, 64)
	if err != nil {
		http.Error(w, "valid revision is required", http.StatusBadRequest)
		return
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	if expected != s.revision || len(s.sessions) != 0 {
		http.Error(w, "terminal session state changed", http.StatusConflict)
		return
	}
	// A lease prevents an operator crash between drain and Deployment scale from
	// permanently wedging the still-running pod. reserve() automatically re-opens it.
	s.drainUntil = time.Now().Add(30 * time.Second)
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(map[string]any{
		"draining": true, "session_revision": s.revision,
	})
}

type clientMessage struct {
	Type string `json:"type"`
	Data string `json:"data,omitempty"`
	Cols int    `json:"cols,omitempty"`
	Rows int    `json:"rows,omitempty"`
}

type wsWriter struct {
	mu sync.Mutex
	c  *websocket.Conn
}

func (w *wsWriter) json(ctx context.Context, value any) error {
	b, err := json.Marshal(value)
	if err != nil {
		return err
	}
	w.mu.Lock()
	defer w.mu.Unlock()
	return w.c.Write(ctx, websocket.MessageText, b)
}

func clamp(value, min, max int) int {
	if value < min {
		return min
	}
	if value > max {
		return max
	}
	return value
}

func shellEnv() []string {
	return []string{
		"HOME=/workspace/.home",
		"USER=app",
		"LOGNAME=app",
		"SHELL=/bin/bash",
		"PATH=/usr/local/bin:/usr/local/sbin:/usr/bin:/usr/sbin:/bin:/sbin",
		"TERM=xterm-256color",
		"COLORTERM=truecolor",
		"LANG=C.UTF-8",
		"LC_ALL=C.UTF-8",
		"TMPDIR=/tmp",
		"WORKSPACE_DIR=/workspace",
		"PRIVA_HOME=/workspace/.priva",
		"CLAUDE_CONFIG_DIR=/workspace/.claude",
		"PRIVA_HOOK_DIR=/workspace/.priva/hook-context",
	}
}

func (s *server) terminal(w http.ResponseWriter, r *http.Request) {
	// The EPP overwrites this header after authentication, and NetworkPolicy
	// permits ingress only from non-tenant infrastructure (agentgateway).
	if r.Header.Get(authHeader) != "1" {
		http.Error(w, "terminal authorization required", http.StatusUnauthorized)
		return
	}
	id, ok := s.reserve()
	if !ok {
		http.Error(w, "terminal session limit reached", http.StatusTooManyRequests)
		return
	}
	defer s.release(id)

	c, err := websocket.Accept(w, r, &websocket.AcceptOptions{
		Subprotocols: []string{"priva.ws.v1"},
	})
	if err != nil {
		return
	}
	c.SetReadLimit(maxInputMessage)
	defer c.Close(websocket.StatusNormalClosure, "session closed")

	initCtx, initCancel := context.WithTimeout(r.Context(), 10*time.Second)
	_, payload, err := c.Read(initCtx)
	initCancel()
	if err != nil {
		return
	}
	var init clientMessage
	if json.Unmarshal(payload, &init) != nil || init.Type != "init" {
		_ = c.Close(websocket.StatusPolicyViolation, "init required")
		return
	}
	cols, rows := clamp(init.Cols, 20, 500), clamp(init.Rows, 5, 200)

	// prlimit is exec'd as the shell's direct parent so every descendant inherits
	// the bounds. RLIMIT_CORE=0 is the in-process core-dump prohibition.
	cmd := exec.Command("/usr/bin/prlimit",
		"--nofile=4096:4096", "--nproc=256:256", "--core=0:0", "--",
		s.cfg.shell, "-l")
	cmd.Dir = s.cfg.cwd
	cmd.Env = shellEnv()
	ptmx, err := pty.StartWithAttrs(cmd, &pty.Winsize{Cols: uint16(cols), Rows: uint16(rows)},
		&syscall.SysProcAttr{Setsid: true, Setctty: true, Ctty: 0})
	if err != nil {
		writer := &wsWriter{c: c}
		ctx, cancel := context.WithTimeout(context.Background(), time.Second)
		_ = writer.json(ctx, map[string]any{"type": "error", "message": "shell start failed"})
		cancel()
		return
	}
	defer ptmx.Close()

	ctx, cancel := context.WithCancel(r.Context())
	defer cancel()
	writer := &wsWriter{c: c}
	if err := writer.json(ctx, map[string]any{
		"type": "ready", "session_id": id, "cwd": s.cfg.cwd, "cols": cols, "rows": rows,
	}); err != nil {
		return
	}

	lastInput := atomic.Int64{}
	lastInput.Store(time.Now().UnixNano())
	started := time.Now()
	reasonCh := make(chan string, 1)
	finish := func(reason string) {
		select {
		case reasonCh <- reason:
			cancel()
		default:
		}
	}

	queueSize := s.cfg.outputBuf / ptyReadChunkSize
	if queueSize < 1 {
		queueSize = 1
	}
	output := make(chan []byte, queueSize)

	go func() {
		buf := make([]byte, ptyReadChunkSize)
		for {
			n, readErr := ptmx.Read(buf)
			if n > 0 {
				chunk := append([]byte(nil), buf[:n]...)
				select {
				case output <- chunk:
				case <-ctx.Done():
					return
				default:
					finish("output_overflow")
					return
				}
			}
			if readErr != nil {
				if !errors.Is(readErr, io.EOF) && !errors.Is(readErr, os.ErrClosed) {
					log.Printf("terminal %s pty read: %v", id, readErr)
				}
				finish("process_exit")
				return
			}
		}
	}()

	go func() {
		tokens := float64(s.cfg.outputBurst)
		lastRefill := time.Now()
		for {
			select {
			case <-ctx.Done():
				return
			case chunk := <-output:
				now := time.Now()
				tokens += now.Sub(lastRefill).Seconds() * float64(s.cfg.outputRate)
				if tokens > float64(s.cfg.outputBurst) {
					tokens = float64(s.cfg.outputBurst)
				}
				lastRefill = now
				if need := float64(len(chunk)) - tokens; need > 0 {
					delay := time.Duration(need / float64(s.cfg.outputRate) * float64(time.Second))
					timer := time.NewTimer(delay)
					select {
					case <-ctx.Done():
						timer.Stop()
						return
					case <-timer.C:
					}
					tokens = 0
					lastRefill = time.Now()
				} else {
					tokens -= float64(len(chunk))
				}
				text := string(chunk)
				if !utf8.ValidString(text) {
					text = strings.ToValidUTF8(text, "\uFFFD")
				}
				if err := writer.json(ctx, map[string]any{"type": "output", "data": text}); err != nil {
					finish("client_close")
					return
				}
				s.touch()
			}
		}
	}()

	go func() {
		for {
			_, data, readErr := c.Read(ctx)
			if readErr != nil {
				finish("client_close")
				return
			}
			var msg clientMessage
			if json.Unmarshal(data, &msg) != nil {
				continue
			}
			switch msg.Type {
			case "input":
				if len(msg.Data) > maxInputMessage {
					finish("input_overflow")
					return
				}
				if _, err := ptmx.Write([]byte(msg.Data)); err != nil {
					finish("process_exit")
					return
				}
				lastInput.Store(time.Now().UnixNano())
				s.touch()
			case "resize":
				_ = pty.Setsize(ptmx, &pty.Winsize{
					Cols: uint16(clamp(msg.Cols, 20, 500)), Rows: uint16(clamp(msg.Rows, 5, 200)),
				})
			case "ping":
				if err := writer.json(ctx, map[string]any{"type": "pong"}); err != nil {
					finish("client_close")
					return
				}
			}
		}
	}()

	go func() {
		err := cmd.Wait()
		if err != nil {
			log.Printf("terminal %s shell exit: %v", id, err)
		}
		finish("process_exit")
	}()

	go func() {
		ticker := time.NewTicker(time.Second)
		defer ticker.Stop()
		for {
			select {
			case <-ctx.Done():
				return
			case now := <-ticker.C:
				last := time.Unix(0, lastInput.Load())
				if now.Sub(last) >= s.cfg.idle {
					finish("idle_timeout")
					return
				}
				if now.Sub(started) >= s.cfg.lifetime {
					finish("absolute_timeout")
					return
				}
			}
		}
	}()

	reason := <-reasonCh
	// The PTY child is a new session/process group. Terminate the whole group so
	// detached grandchildren cannot outlive a closed browser session.
	if cmd.Process != nil {
		_ = syscall.Kill(-cmd.Process.Pid, syscall.SIGTERM)
		time.Sleep(250 * time.Millisecond)
		_ = syscall.Kill(-cmd.Process.Pid, syscall.SIGKILL)
	}
	code := 1000
	switch reason {
	case "idle_timeout":
		code = 4010
	case "absolute_timeout":
		code = 4011
	case "output_overflow", "input_overflow":
		code = 1009
	}
	closeCtx, closeCancel := context.WithTimeout(context.Background(), time.Second)
	_ = writer.json(closeCtx, map[string]any{"type": "closed", "reason": reason})
	closeCancel()
	_ = c.Close(websocket.StatusCode(code), reason)
}

func main() {
	cfg := loadConfig()
	for _, dir := range []string{cfg.cwd, "/workspace/.home", "/workspace/.priva", "/workspace/.claude"} {
		if err := os.MkdirAll(dir, 0700); err != nil {
			log.Fatalf("prepare terminal workspace %s: %v", dir, err)
		}
	}
	s := newServer(cfg)
	mux := http.NewServeMux()
	mux.HandleFunc("/health", s.health)
	mux.HandleFunc("/internal/drain", s.drain)
	mux.HandleFunc("/api/terminal/ws", s.terminal)
	server := &http.Server{
		Addr:              cfg.listen,
		Handler:           mux,
		ReadHeaderTimeout: 5 * time.Second,
		IdleTimeout:       65 * time.Second,
		MaxHeaderBytes:    16 * 1024,
	}
	log.Printf("priva-terminald listening on %s max_sessions=%d", cfg.listen, cfg.maxSessions)
	if err := server.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
		log.Fatal(fmt.Errorf("terminal server: %w", err))
	}
}
