package main

import (
	"crypto"
	"crypto/rand"
	"crypto/rsa"
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

func testServer() *server {
	return newServer(config{maxSessions: 2, idle: time.Hour, lifetime: time.Hour})
}

func authTestServer(t *testing.T) (*server, *rsa.PrivateKey) {
	t.Helper()
	privateKey, err := rsa.GenerateKey(rand.Reader, 2048)
	if err != nil {
		t.Fatal(err)
	}
	return newServer(config{
		maxSessions:    2,
		idle:           time.Hour,
		lifetime:       time.Hour,
		authPublicKeys: []*rsa.PublicKey{&privateKey.PublicKey},
		accountID:      "acct-1",
		pod:            "10.2.3.4",
		drainToken:     "pod-drain-capability",
	}), privateKey
}

func signTestToken(
	t *testing.T, privateKey *rsa.PrivateKey, claims map[string]any,
) string {
	t.Helper()
	headerJSON, err := json.Marshal(map[string]string{"alg": "RS256", "typ": "JWT"})
	if err != nil {
		t.Fatal(err)
	}
	claimsJSON, err := json.Marshal(claims)
	if err != nil {
		t.Fatal(err)
	}
	encode := base64.RawURLEncoding.EncodeToString
	signed := encode(headerJSON) + "." + encode(claimsJSON)
	digest := sha256.Sum256([]byte(signed))
	signature, err := rsa.SignPKCS1v15(
		rand.Reader, privateKey, crypto.SHA256, digest[:],
	)
	if err != nil {
		t.Fatal(err)
	}
	return signed + "." + encode(signature)
}

func operatorToken(t *testing.T, privateKey *rsa.PrivateKey) string {
	t.Helper()
	now := time.Now().Unix()
	return signTestToken(t, privateKey, map[string]any{
		"typ": "service",
		"svc": "operator",
		"iat": now,
		"exp": now + 3600,
	})
}

func terminalClaims() map[string]any {
	now := time.Now().Unix()
	return map[string]any{
		"typ":        "terminal",
		"aud":        "priva-terminal",
		"account_id": "acct-1",
		"pod":        "10.2.3.4",
		"iat":        now,
		"exp":        now + 30,
	}
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

func TestHealthRequiresPerPodCapability(t *testing.T) {
	s, _ := authTestServer(t)

	req := httptest.NewRequest(http.MethodGet, "/health", nil)
	w := httptest.NewRecorder()
	s.health(w, req)
	if w.Code != http.StatusUnauthorized {
		t.Fatalf("anonymous health got %d, want 401", w.Code)
	}

	req = httptest.NewRequest(http.MethodGet, "/health", nil)
	req.Header.Set(drainAuthHeader, "pod-drain-capability")
	w = httptest.NewRecorder()
	s.health(w, req)
	if w.Code != http.StatusOK {
		t.Fatalf("capability health got %d, want 200", w.Code)
	}
	if !strings.Contains(w.Body.String(), `"session_revision":0`) {
		t.Fatalf("health response omitted session revision: %s", w.Body.String())
	}
}

func TestDrainRejectsConcurrentSessionRevision(t *testing.T) {
	s, _ := authTestServer(t)
	_, ok := s.reserve()
	if !ok {
		t.Fatal("session reserve failed")
	}
	req := httptest.NewRequest(http.MethodPost, "/internal/drain?revision=0", nil)
	req.Header.Set(drainAuthHeader, "pod-drain-capability")
	w := httptest.NewRecorder()
	s.drain(w, req)
	if w.Code != http.StatusConflict {
		t.Fatalf("got %d, want 409", w.Code)
	}
}

func TestDrainWithoutForceRejectsActiveSessionAtCurrentRevision(t *testing.T) {
	s, _ := authTestServer(t)
	if _, ok := s.reserve(); !ok {
		t.Fatal("session reserve failed")
	}
	req := httptest.NewRequest(http.MethodPost, "/internal/drain?revision=1", nil)
	req.Header.Set(drainAuthHeader, "pod-drain-capability")
	w := httptest.NewRecorder()
	s.drain(w, req)
	if w.Code != http.StatusConflict {
		t.Fatalf("got %d, want 409", w.Code)
	}
	if _, _, draining := s.state(); draining {
		t.Fatal("non-forced drain closed admission while a session was active")
	}
}

func TestSuccessfulDrainPermanentlyRejectsNewSessions(t *testing.T) {
	s, _ := authTestServer(t)
	req := httptest.NewRequest(http.MethodPost, "/internal/drain?revision=0", nil)
	req.Header.Set(drainAuthHeader, "pod-drain-capability")
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

func TestDrainAcceptsPerPodCapabilityWithoutServiceJWT(t *testing.T) {
	s, _ := authTestServer(t)
	req := httptest.NewRequest(http.MethodPost, "/internal/drain?revision=0", nil)
	req.Header.Set(drainAuthHeader, "pod-drain-capability")
	w := httptest.NewRecorder()
	s.drain(w, req)
	if w.Code != http.StatusOK {
		t.Fatalf("got %d, want 200", w.Code)
	}
	if _, ok := s.reserve(); ok {
		t.Fatal("reserve succeeded after capability-authorized drain")
	}
}

func TestForcedDrainKeepsExistingSessionAndPermanentlyClosesAdmission(t *testing.T) {
	s, _ := authTestServer(t)
	id, ok := s.reserve()
	if !ok {
		t.Fatal("session reserve failed")
	}

	req := httptest.NewRequest(
		http.MethodPost, "/internal/drain?force=true", nil,
	)
	req.Header.Set(drainAuthHeader, "pod-drain-capability")
	w := httptest.NewRecorder()
	s.drain(w, req)
	if w.Code != http.StatusOK {
		t.Fatalf("got %d, want 200", w.Code)
	}
	active, revision, draining := s.state()
	if active != 1 || revision != 1 || !draining {
		t.Fatalf(
			"forced drain did not preserve session: active=%d revision=%d draining=%v",
			active, revision, draining,
		)
	}
	if _, ok := s.reserve(); ok {
		t.Fatal("new session entered after forced drain")
	}

	s.release(id)
	active, revision, draining = s.state()
	if active != 0 || revision != 2 || !draining {
		t.Fatalf(
			"drain reopened after existing session left: active=%d revision=%d draining=%v",
			active, revision, draining,
		)
	}
	if _, ok := s.reserve(); ok {
		t.Fatal("new session entered after drained session completed")
	}
}

func TestTerminalTokenAuthorization(t *testing.T) {
	s, privateKey := authTestServer(t)
	otherKey, err := rsa.GenerateKey(rand.Reader, 2048)
	if err != nil {
		t.Fatal(err)
	}

	tests := []struct {
		name       string
		mutate     func(map[string]any)
		signingKey *rsa.PrivateKey
		wantError  bool
	}{
		{name: "valid", wantError: false},
		{name: "bad signature", signingKey: otherKey, wantError: true},
		{name: "expired", mutate: func(c map[string]any) {
			now := time.Now().Unix()
			c["iat"], c["exp"] = now-90, now-60
		}, wantError: true},
		{name: "wrong type", mutate: func(c map[string]any) {
			c["typ"] = "runner"
		}, wantError: true},
		{name: "wrong audience", mutate: func(c map[string]any) {
			c["aud"] = "other-service"
		}, wantError: true},
		{name: "wrong account", mutate: func(c map[string]any) {
			c["account_id"] = "acct-2"
		}, wantError: true},
		{name: "wrong pod", mutate: func(c map[string]any) {
			c["pod"] = "10.2.3.99"
		}, wantError: true},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			claims := terminalClaims()
			if tt.mutate != nil {
				tt.mutate(claims)
			}
			key := tt.signingKey
			if key == nil {
				key = privateKey
			}
			err := s.authorizeTerminal(signTestToken(t, key, claims))
			if (err != nil) != tt.wantError {
				t.Fatalf("authorizeTerminal() error = %v, wantError=%v", err, tt.wantError)
			}
		})
	}
}

func TestTerminalTokenAcceptsAdditionalRotationKey(t *testing.T) {
	s, oldPrivateKey := authTestServer(t)
	newPrivateKey, err := rsa.GenerateKey(rand.Reader, 2048)
	if err != nil {
		t.Fatal(err)
	}
	s.cfg.authPublicKeys = []*rsa.PublicKey{
		&newPrivateKey.PublicKey,
		&oldPrivateKey.PublicKey,
	}

	if err := s.authorizeTerminal(
		signTestToken(t, oldPrivateKey, terminalClaims()),
	); err != nil {
		t.Fatalf("token signed by overlap key was rejected: %v", err)
	}
}

func TestDrainRejectsMissingOrWrongCapabilityEvenWithOperatorToken(t *testing.T) {
	s, privateKey := authTestServer(t)

	req := httptest.NewRequest(http.MethodPost, "/internal/drain?revision=0", nil)
	req.Header.Set("X-Priva-Service-Token", operatorToken(t, privateKey))
	w := httptest.NewRecorder()
	s.drain(w, req)
	if w.Code != http.StatusUnauthorized {
		t.Fatalf("operator token without capability got %d, want 401", w.Code)
	}

	req = httptest.NewRequest(http.MethodPost, "/internal/drain?revision=0", nil)
	req.Header.Set(drainAuthHeader, "wrong-capability")
	req.Header.Set("X-Priva-Service-Token", operatorToken(t, privateKey))
	w = httptest.NewRecorder()
	s.drain(w, req)
	if w.Code != http.StatusUnauthorized {
		t.Fatalf("wrong capability got %d, want 401", w.Code)
	}
}
