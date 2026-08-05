package discord

import (
	"fmt"
	"os"
	"path/filepath"

	"github.com/bwmarrin/discordgo"

	"github.com/nextlevelbuilder/goclaw/internal/bus"
)

// sendMediaMessage sends media attachments to a Discord channel using file uploads.
// Text content (if any) is included as the message body alongside the first attachment.
func (c *Channel) sendMediaMessage(channelID string, content string, mediaList []bus.MediaAttachment) error {
	var files []*discordgo.File

	for _, att := range mediaList {
		filePath := att.URL
		if filePath == "" {
			continue
		}

		maxBytes := c.config.MediaMaxBytes
		if maxBytes <= 0 {
			maxBytes = defaultMediaMaxBytes
		}

		f, err := os.Open(filePath)
		if err != nil {
			return fmt.Errorf("open media file %s: %w", filePath, err)
		}
		defer f.Close()

		if info, err := f.Stat(); err == nil && info.Size() > maxBytes {
			return fmt.Errorf("outbound media too large: %d bytes (limit %d)", info.Size(), maxBytes)
		}

		ct := att.ContentType
		if ct == "" {
			ct = "application/octet-stream"
		}

		files = append(files, &discordgo.File{
			Name:        filepath.Base(filePath),
			ContentType: ct,
			Reader:      f,
		})
	}

	if len(files) == 0 {
		return nil
	}

	// Discord supports multiple files + text in a single message.
	msg := &discordgo.MessageSend{
		Files: files,
	}

	// Attach text content if provided. Fail closed instead of truncating so
	// outbound review text remains byte-for-byte identical to audited content.
	const maxDiscordMessageBytes = 2000
	if content != "" {
		if len([]byte(content)) > maxDiscordMessageBytes {
			return fmt.Errorf("discord media message content too long: %d bytes (limit %d)", len([]byte(content)), maxDiscordMessageBytes)
		}
		msg.Content = content
	}

	_, err := c.session.ChannelMessageSendComplex(channelID, msg)
	if err != nil {
		return fmt.Errorf("send discord media message: %w", err)
	}
	return nil
}
