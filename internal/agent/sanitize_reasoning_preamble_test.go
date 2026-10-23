package agent

import (
	"strings"
	"testing"
)

// Shape taken from a stored Zip reply on 2026-10-23 (shortened): a Chinese
// header, an English plan, and the Vietnamese answer glued on with no newline.
const leakedGeminiReply = "思考过程：\n\n1.  **Understand the User's Request**: continue to Chapter VIII.\n" +
	"2.  **Apply Mandatory Rules**:\n    *   Persona: Zip.\n" +
	"6.  **Review and Refine**: Keep the tone energetic. Let's write the response!" +
	"Quất tiếp **Chương VIII (Quyển IV)** nha sếp! 👾🔥\n\n### I. NỘI DUNG CỐT LÕI"

func TestStripLeakedReasoningPreamble(t *testing.T) {
	tests := []struct {
		name string
		in   string
		want string
	}{
		{
			name: "gemini chinese header glued to the answer",
			in:   leakedGeminiReply,
			want: "Quất tiếp **Chương VIII (Quyển IV)** nha sếp! 👾🔥\n\n### I. NỘI DUNG CỐT LÕI",
		},
		{
			name: "english header handed off to the answer",
			in:   "**Thinking Process:**\n1. Plan the answer.\nLet's draft the reply.\nXin chào bạn!",
			want: "Xin chào bạn!",
		},
		{
			name: "requested working that ends in a final answer label is kept",
			in:   "Thinking Process:\n1. 6 x 7 = 42\n\nFinal Answer: 42",
			want: "Thinking Process:\n1. 6 x 7 = 42\n\nFinal Answer: 42",
		},
		{
			name: "header without a hand-off is kept whole",
			in:   "思考过程：\n1. plan only, the model never handed off",
			want: "思考过程：\n1. plan only, the model never handed off",
		},
		{
			name: "hand-off phrase without the header is a normal answer",
			in:   "Here is how I work. Let's write the response! Then we review it.",
			want: "Here is how I work. Let's write the response! Then we review it.",
		},
		{
			name: "header mentioned mid-message is not a preamble",
			in:   "Bạn hỏi về 思考过程： đó là quá trình suy nghĩ. Let's draft the answer.",
			want: "Bạn hỏi về 思考过程： đó là quá trình suy nghĩ. Let's draft the answer.",
		},
		{
			name: "hand-off with nothing after it keeps the content",
			in:   "Thinking Process: plan. Let's write the response!",
			want: "Thinking Process: plan. Let's write the response!",
		},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			if got := stripLeakedReasoningPreamble(tt.in); got != tt.want {
				t.Errorf("got %q\nwant %q", got, tt.want)
			}
		})
	}
}

func TestSanitizeAssistantContent_DropsLeakedReasoningPlan(t *testing.T) {
	got := SanitizeAssistantContent(leakedGeminiReply)
	if strings.Contains(got, "思考过程") || strings.Contains(got, "Understand the User") {
		t.Fatalf("plan survived sanitization: %q", got)
	}
	if !strings.HasPrefix(got, "Quất tiếp") {
		t.Fatalf("answer lost or mangled: %q", got)
	}
}
