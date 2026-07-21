package main

import (
	"strings"
	"testing"
)

func TestSessionLimit(t *testing.T) {
	s := newServer(config{maxSessions: 2})
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
	for _, required := range []string{"HOME=/workspace/.home", "CLAUDE_CONFIG_DIR=/workspace/.claude"} {
		if !strings.Contains(env, required) {
			t.Fatalf("shell environment is missing %q", required)
		}
	}
}
