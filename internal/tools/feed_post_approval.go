package tools

import (
	"strings"
	"unicode"

	"golang.org/x/text/unicode/norm"
)

// MetaContentFactoryReviewDraft marks an outbound message as a review draft
// awaiting approval, so the Discord channel can tag it for the approval
// shortcuts (✅ reaction, bare "duyệt").
const MetaContentFactoryReviewDraft = "cf_review_draft"

// maxApprovalCommandWords bounds how long an approval command may be. An
// approval is a short command; anything longer is a sentence the model would
// have to interpret, and interpretation is exactly what this gate avoids.
const maxApprovalCommandWords = 10

// approvalKeywords are the words that carry the approval itself. At least one
// must be present.
var approvalKeywords = map[string]bool{
	"duyệt":    true,
	"duyet":    true,
	"đăng":     true,
	"approve":  true,
	"approved": true,
	"post":     true,
}

// approvalFillers are the only other words an approval command may contain:
// politeness, pronouns, and "this/now" pointers. The list is an allowlist on
// purpose. A denylist of negations has to anticipate every way to say "no"
// ("chưa", "đừng", "khoan", "để sau", "sửa lại"...); an allowlist fails closed
// on anything it has not seen, so "duyệt nhưng sửa tiêu đề" or
// "đăng bài mới về Nvidia" are never read as approvals.
var approvalFillers = map[string]bool{
	"ok": true, "oke": true, "okay": true, "okie": true, "ừ": true, "ừm": true,
	"được": true, "đc": true, "rồi": true, "đã": true, "thôi": true,
	"bài": true, "này": true, "nhé": true, "nhe": true, "nha": true,
	"ngay": true, "đi": true, "luôn": true, "lên": true, "giúp": true, "hộ": true,
	"em": true, "anh": true, "chị": true, "bạn": true, "mình": true,
	"fanpage": true, "page": true,
	"please": true, "pls": true, "this": true, "it": true, "now": true, "go": true,
}

// IsPositiveFeedPostApproval reports whether message is an unambiguous command
// to publish the reviewed draft: at least one approval keyword, every other
// word from the filler allowlist, no question, and short. Emoji and
// punctuation are ignored, so "Duyệt nhé em 👍" and "ok, đăng luôn!" pass.
//
// The Discord channel uses this to decide whether a non-reply message may be
// bound to the pending review draft, and the message tool uses it as the
// final approval check, so both layers agree on what counts as approval.
func IsPositiveFeedPostApproval(message string) bool {
	// NFC so a keyboard that emits decomposed Vietnamese diacritics still
	// matches the precomposed keys above.
	msg := strings.ToLower(norm.NFC.String(message))
	if strings.ContainsAny(msg, "?？") {
		return false
	}
	words := strings.FieldsFunc(msg, func(r rune) bool {
		return !unicode.IsLetter(r) && !unicode.IsNumber(r) && !unicode.Is(unicode.Mn, r)
	})
	if len(words) == 0 || len(words) > maxApprovalCommandWords {
		return false
	}
	hasKeyword := false
	for _, w := range words {
		switch {
		case approvalKeywords[w]:
			hasKeyword = true
		case approvalFillers[w]:
		default:
			return false
		}
	}
	return hasKeyword
}
