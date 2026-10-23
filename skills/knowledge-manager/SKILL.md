---
name: knowledge-manager
description: Use this skill when the user wants to upload knowledge, documents, or information to an agent so it can use that knowledge to answer questions. This includes uploading FAQs, product info, company policies, training materials, or any reference documents. Also use when the user wants to manage, search, index, update, or delete agent knowledge. If the user says "upload knowledge", "add documents", "teach agent", "agent needs to know", "knowledge base", or "RAG", use this skill.
metadata:
  author: Commander
  version: "1.0.0"
---

# Knowledge Manager — Upload & Manage Agent Knowledge

## How Knowledge Works in GoClaw

Agents have two knowledge systems:
1. **Memory Documents** — text files indexed for semantic search (RAG). Agent uses `memory_search` tool to find relevant content when answering.
2. **Knowledge Graph** — structured entities with relationships. Agent uses `knowledge_graph_search` for traversing connections between concepts.

Both require the agent to have `memory_config.enabled = true`.

## Upload Single Document

```bash
# 1. Upload content
curl -s -X PUT -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: text/plain" \
  --data-binary 'Your document content here.

This can be multi-line, formatted as markdown.
Include headers, lists, tables — anything useful.

## Section 1
Important information here.

## Section 2
More information here.' \
  "$URL/v1/agents/{agentID}/memory/documents/knowledge/{filename}.md"

# 2. Index for semantic search
curl -s -X POST -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"path":"knowledge/{filename}.md"}' \
  "$URL/v1/agents/{agentID}/memory/index"

# 3. Verify it's searchable
curl -s -X POST -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"query":"test question about the content"}' \
  "$URL/v1/agents/{agentID}/memory/search"
```

## Upload Multiple Documents (Bulk)

```bash
# Upload each document
for file in product-info faq policies pricing; do
  curl -s -X PUT -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: text/plain" \
    --data-binary "@docs/${file}.md" \
    "$URL/v1/agents/{agentID}/memory/documents/knowledge/${file}.md"
done

# Index all at once
curl -s -X POST -H "Authorization: Bearer $TOKEN" \
  "$URL/v1/agents/{agentID}/memory/index-all"
```

When using Commander (not raw curl), write each document with `write_file` then upload via `exec curl`.

## Document Organization

Recommended path structure:
```
knowledge/
├── products/
│   ├── product-catalog.md
│   ├── pricing.md
│   └── features.md
├── policies/
│   ├── return-policy.md
│   ├── warranty.md
│   └── privacy.md
├── faq/
│   ├── general-faq.md
│   └── technical-faq.md
└── training/
    ├── onboarding.md
    └── procedures.md
```

Use descriptive paths — they help organize and manage documents. The full path is: `/v1/agents/{id}/memory/documents/knowledge/products/pricing.md`

## Manage Existing Knowledge

```bash
# List all documents for an agent
curl -s -H "Authorization: Bearer $TOKEN" \
  "$URL/v1/agents/{agentID}/memory/documents"

# Read specific document
curl -s -H "Authorization: Bearer $TOKEN" \
  "$URL/v1/agents/{agentID}/memory/documents/knowledge/faq.md"

# Update document (same as create — PUT overwrites)
curl -s -X PUT -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: text/plain" \
  --data-binary 'Updated content here' \
  "$URL/v1/agents/{agentID}/memory/documents/knowledge/faq.md"

# Re-index after update
curl -s -X POST -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"path":"knowledge/faq.md"}' \
  "$URL/v1/agents/{agentID}/memory/index"

# Delete document
curl -s -X DELETE -H "Authorization: Bearer $TOKEN" \
  "$URL/v1/agents/{agentID}/memory/documents/knowledge/old-doc.md"

# View indexed chunks (debug)
curl -s -H "Authorization: Bearer $TOKEN" \
  "$URL/v1/agents/{agentID}/memory/chunks?path=knowledge/faq.md"
```

## Knowledge Graph (Structured Data)

For data with relationships (people, projects, products, categories):

```bash
# Create entity
curl -s -X POST -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Product ABC",
    "type": "product",
    "observations": [
      "Price: $99/month",
      "Launched: 2025-01",
      "Category: SaaS",
      "Key feature: AI-powered analytics"
    ]
  }' "$URL/v1/agents/{agentID}/kg/entities"

# Create related entity
curl -s -X POST -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Enterprise Plan",
    "type": "concept",
    "observations": ["Includes all features", "Custom support", "$499/month"],
    "relations": [{"target": "Product ABC", "type": "belongs_to"}]
  }' "$URL/v1/agents/{agentID}/kg/entities"

# Auto-extract entities from text (uses LLM)
curl -s -X POST -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "text": "John manages the Sales team. The Sales team uses Product ABC for analytics. Product ABC was created by the Engineering team led by Sarah."
  }' "$URL/v1/agents/{agentID}/kg/extract"

# Search entities
curl -s -H "Authorization: Bearer $TOKEN" \
  "$URL/v1/agents/{agentID}/kg/entities?query=Product"

# Traverse relationships
curl -s -X POST -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"entity_id": "{entity-uuid}", "depth": 2}' \
  "$URL/v1/agents/{agentID}/kg/traverse"

# Get stats
curl -s -H "Authorization: Bearer $TOKEN" \
  "$URL/v1/agents/{agentID}/kg/stats"
```

## Best Practices

### Document Format
- Use **Markdown** with clear headers, lists, and tables
- Keep documents focused — one topic per file
- Include Q&A pairs where possible (improves search relevance)
- Add metadata at the top: last updated, version, author

### Chunking
- Documents are automatically split into chunks for indexing
- Each chunk ~1000 characters at paragraph boundaries
- Short, focused documents search better than long monolithic ones

### Search Quality
- Test search after upload to verify relevance
- Use natural language queries (same as how users will ask)
- If search misses content, try rewriting with different keywords
- Re-index after any update: always call `/memory/index` after PUT

### When to Use Documents vs Knowledge Graph
| Use Case | Best Choice |
|----------|-------------|
| Product info, FAQ, policies | Documents (semantic search) |
| Organization structure | Knowledge Graph (relationships) |
| Procedures, how-to guides | Documents |
| People, teams, projects | Knowledge Graph |
| Pricing, specifications | Documents |
| Dependencies, connections | Knowledge Graph |
| Training materials | Documents |

### Prerequisites
- Agent must have `memory_config.enabled = true`
- For Knowledge Graph: agent needs `knowledge_graph` capability
- Embedding provider must be configured (for semantic search indexing)
