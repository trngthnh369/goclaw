-- Create Market Research daily cron job at 9:30 AM VN (02:30 UTC)
INSERT INTO cron_jobs (
  name, 
  schedule_kind, schedule_expr, schedule_tz,
  agent_id, 
  payload,
  enabled,
  tenant_id,
  user_id
) VALUES (
  'market-research-daily',
  'cron', '30 2 * * *', 'Asia/Ho_Chi_Minh',
  (SELECT id FROM agents WHERE agent_key = 'market-lead'),
  '{"kind": "agent_turn", "channel": "market-research-team", "to": "896694335670726676", "deliver": true, "instruction": "Run daily market research report. Dispatch tasks to Market Scout for data collection and Fact Checker for verification. Synthesize into a complete report."}'::jsonb,
  true,
  (SELECT tenant_id FROM agents WHERE agent_key = 'market-lead'),
  'system'
) ON CONFLICT DO NOTHING
RETURNING id, name, schedule_expr, schedule_tz, enabled;
