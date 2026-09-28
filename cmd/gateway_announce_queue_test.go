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
	return announceEntry{MemberAgent: member, Content: "done", OriginSenderID: "896694335670726676", OriginRole: "owner"}
}

func TestBuildTeamAnnounceRunRequest_GroupCarriesOriginContext(t *testing.T) {
	r := codexGroupRouting()

	req := buildTeamAnnounceRunRequest(r, []announceEntry{ownerEntry("agy-pro"), ownerEntry("agy-flash")}, "[System Message] done")

	checks := map[string][2]string{
		"ChannelType": {req.ChannelType, "discord"},
		"Channel":     {req.Channel, "codex-discord"},
		"UserID":      {req.UserID, r.OriginUserID},
		"SenderID":    {req.SenderID, "896694335670726676"},
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

	if req.SenderID != "" || req.Role != "" {
		t.Errorf("SenderID=%q Role=%q, want both empty for a batch mixing two users", req.SenderID, req.Role)
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
