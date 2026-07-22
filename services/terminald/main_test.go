package main

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

func testServer() *server {
	return newServer(config{maxSessions: 2, idle: time.Hour, lifetime: time.Hour})
}

func TestSessionLimit(t *testing.T) {
	s := testServer()
	first, ok := s.reserve()
	if !ok {
		t.Fatal("first session was rejected")
	}
	if _, ok = s.reserve(); !ok {
		t.Fatal("second session was rejected")
	}
	if _, ok = s.reserve(); ok {
		t.Fatal("third session exceeded the configured limit")
	}
	s.release(first)
	if _, ok = s.reserve(); !ok {
		t.Fatal("released capacity was not reusable")
	}
}

func TestShellEnvironmentIsAllowListed(t *testing.T) {
	env := strings.Join(shellEnv(), "\n")
	for _, secret := range []string{"JWT_SECRET", "POSTGRES", "REDIS", "DATA_SPINE"} {
		if strings.Contains(env, secret) {
			t.Fatalf("shell environment contains forbidden key fragment %q", secret)
		}
	}
	for _, required := range []string{
		"HOME=/workspace/.home", "CLAUDE_CONFIG_DIR=/workspace/.claude",
	} {
		if !strings.Contains(env, required) {
			t.Fatalf("shell environment is missing %q", required)
		}
	}
}

func TestDrainRejectsConcurrentSessionRevision(t *testing.T) {
	s := testServer()
	_, ok := s.reserve()
	if !ok {
		t.Fatal("session reserve failed")
	}
	req := httptest.NewRequest(http.MethodPost, "/internal/drain?revision=0", nil)
	w := httptest.NewRecorder()
	s.drain(w, req)
	if w.Code != http.StatusConflict {
		t.Fatalf("got %d, want 409", w.Code)
	}
}

func TestSuccessfulDrainAtomicallyRejectsNewSessions(t *testing.T) {
	s := testServer()
	req := httptest.NewRequest(http.MethodPost, "/internal/drain?revision=0", nil)
	w := httptest.NewRecorder()
	s.drain(w, req)
	if w.Code != http.StatusOK {
		t.Fatalf("got %d, want 200", w.Code)
	}
	if _, ok := s.reserve(); ok {
		t.Fatal("reserve succeeded while draining")
	}
	active, revision, draining := s.state()
	if active != 0 || revision != 0 || !draining {
		t.Fatalf("unexpected state active=%d revision=%d draining=%v", active, revision, draining)
	}
}
