package cmd

import (
	"testing"
)

func codexGroupRouting() announceRouting {
	return announceRouting{
		LeadAgent:       "codex",
		LeadSessionKey:  "agent:codex:codex-discord:group:1552179009540857876",
		OrigChannel:     "codex-discord",
		OrigChannelType: "discord",
		OrigChatID:      "1552179009540857876",
		OrigPeerKind:    "group",
		OriginUserID:    "guild:1487758328577921066:user:896694335670726676",
		TeamID:          "01a0d266-6f71-754e-962f-b81e906b4e63",
	}
}

func ownerEntry(member string) announceEntry {
	return announceEntry{MemberAgent: member, Content: "done", OriginSenderID: "896694335670726676", OriginSenderName: "Turti", OriginRole: "owner"}
}

func TestBuildTeamAnnounceRunRequest_GroupCarriesOriginContext(t *testing.T) {
	r := codexGroupRouting()

	req := buildTeamAnnounceRunRequest(r, []announceEntry{ownerEntry("agy-pro"), ownerEntry("agy-flash")}, "[System Message] done")

	checks := map[string][2]string{
		"ChannelType": {req.ChannelType, "discord"},
		"Channel":     {req.Channel, "codex-discord"},
		"UserID":      {req.UserID, r.OriginUserID},
		"SenderID":    {req.SenderID, "896694335670726676"},
		"SenderName":  {req.SenderName, "Turti"},
		"Role":        {req.Role, "owner"},
		"RunKind":     {req.RunKind, "announce"},
		"RunID":       {req.RunID, "teammate-announce-codex-2"},
	}
	for field, gw := range checks {
		if gw[0] != gw[1] {
			t.Errorf("%s = %q, want %q", field, gw[0], gw[1])
		}
	}
	if req.ExtraSystemPrompt != groupChatExtraPrompt() {
		t.Errorf("ExtraSystemPrompt = %q, want the group guidance of the user's turn", req.ExtraSystemPrompt)
	}
	if !req.HideInput {
		t.Error("HideInput = false, want true")
	}
}

func TestBuildTeamAnnounceRunRequest_MixedOriginBatch_DropsPrivilege(t *testing.T) {
	other := announceEntry{MemberAgent: "agy-flash", Content: "done", OriginSenderID: "111", OriginRole: ""}

	req := buildTeamAnnounceRunRequest(codexGroupRouting(), []announceEntry{ownerEntry("agy-pro"), other}, "x")

	if req.SenderID != "" || req.SenderName != "" || req.Role != "" {
		t.Errorf("SenderID=%q SenderName=%q Role=%q, want all empty for a batch mixing two users", req.SenderID, req.SenderName, req.Role)
	}
}

func TestBuildTeamAnnounceRunRequest_SameSenderDifferentRole_DropsPrivilege(t *testing.T) {
	demoted := ownerEntry("agy-flash")
	demoted.OriginRole = ""

	req := buildTeamAnnounceRunRequest(codexGroupRouting(), []announceEntry{ownerEntry("agy-pro"), demoted}, "x")

	if req.SenderID != "" || req.Role != "" {
		t.Errorf("SenderID=%q Role=%q, want both empty when roles disagree", req.SenderID, req.Role)
	}
}

func TestBuildTeamAnnounceRunRequest_DirectChatHasNoGroupGuidance(t *testing.T) {
	req := buildTeamAnnounceRunRequest(announceRouting{OrigPeerKind: "direct"}, []announceEntry{ownerEntry("m")}, "x")
	if req.ExtraSystemPrompt != "" {
		t.Errorf("ExtraSystemPrompt = %q, want empty for a direct chat", req.ExtraSystemPrompt)
	}
}

func TestAnnounceSenderID_DropsInternalSenders(t *testing.T) {
	cases := map[string]string{
		"896694335670726676": "896694335670726676",
		"teammate:dashboard": "",
		"system:ticker":      "",
		"":                   "",
	}
	for in, want := range cases {
		if got := announceSenderID(in); got != want {
			t.Errorf("announceSenderID(%q) = %q, want %q", in, got, want)
		}
	}
}

func TestAnnounceSenderName_FollowsKeptSender(t *testing.T) {
	cases := []struct {
		name string
		meta map[string]string
		want string
	}{
		{"real sender", map[string]string{"origin_sender_id": "896694335670726676", "origin_sender_name": "Turti"}, "Turti"},
		{"internal sender", map[string]string{"origin_sender_id": "teammate:dashboard", "origin_sender_name": "Turti"}, ""},
		{"no sender", map[string]string{"origin_sender_name": "Turti"}, ""},
		{"name is flattened", map[string]string{"origin_sender_id": "1", "origin_sender_name": "Tur\nti"}, "Tur ti"},
	}
	for _, tc := range cases {
		if got := announceSenderName(tc.meta); got != tc.want {
			t.Errorf("%s: announceSenderName = %q, want %q", tc.name, got, tc.want)
		}
	}
}

func TestCommonOriginPrivilege(t *testing.T) {
	pairs := func(p ...[2]string) func(int) (string, string) {
		return func(i int) (string, string) { return p[i][0], p[i][1] }
	}
	cases := []struct {
		name       string
		items      [][2]string
		wantSender string
		wantRole   string
		wantMixed  bool
	}{
		{"empty batch", nil, "", "", false},
		{"single item", [][2]string{{"u1", "owner"}}, "u1", "owner", false},
		{"all agree", [][2]string{{"u1", "owner"}, {"u1", "owner"}}, "u1", "owner", false},
		{"sender differs", [][2]string{{"u1", "owner"}, {"u2", "owner"}}, "", "", true},
		{"role differs", [][2]string{{"u1", "owner"}, {"u1", ""}}, "", "", true},
		{"same id, new display name", [][2]string{{"u1|Old", "owner"}, {"u1|New", "owner"}}, "u1|Old", "owner", false},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			s, r, mixed := commonOriginPrivilege(len(tc.items), pairs(tc.items...))
			if s != tc.wantSender || r != tc.wantRole || mixed != tc.wantMixed {
				t.Errorf("got (%q, %q, %v), want (%q, %q, %v)", s, r, mixed, tc.wantSender, tc.wantRole, tc.wantMixed)
			}
		})
	}
}

func TestAnnounceBatchUserID(t *testing.T) {
	const alice = "guild:1487758328577921066:user:896694335670726676"
	r := codexGroupRouting()
	one := announceEntry{OriginUserID: alice}
	other := announceEntry{OriginUserID: "guild:1487758328577921066:user:111"}

	if got := announceBatchUserID(r, []announceEntry{one, one}); got != alice {
		t.Errorf("single-user batch = %q, want %q", got, alice)
	}
	if got, want := announceBatchUserID(r, []announceEntry{one, other}), "group:codex-discord:1552179009540857876"; got != want {
		t.Errorf("mixed-user group batch = %q, want the shared group scope %q", got, want)
	}
	if got := announceBatchUserID(r, []announceEntry{{}}); got != r.OriginUserID {
		t.Errorf("entry without user = %q, want routing fallback %q", got, r.OriginUserID)
	}
}

func TestBuildTeamAnnounceRunRequest_SameSenderTakesFirstStoredName(t *testing.T) {
	legacy := ownerEntry("agy-pro")
	legacy.OriginSenderName = "" // task created before names were stored

	req := buildTeamAnnounceRunRequest(codexGroupRouting(), []announceEntry{legacy, ownerEntry("agy-flash")}, "x")

	if req.SenderID != "896694335670726676" || req.SenderName != "Turti" {
		t.Errorf("SenderID=%q SenderName=%q, want the shared sender with its stored name", req.SenderID, req.SenderName)
	}
}
