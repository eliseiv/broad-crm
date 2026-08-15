import type { AiKey } from '@/types/api';

/** Базовые поля AiKey для тестов (включая balance ADR-070). */
export const AI_KEY_TEST_DEFAULTS: Omit<AiKey, 'id' | 'name' | 'provider'> = {
  key_masked: 'sk-p…bA3T',
  check_status: 'working',
  error_message: null,
  position: 0,
  backend_count: 0,
  last_checked_at: '2026-07-01T10:15:00Z',
  created_at: '2026-07-01T09:00:00Z',
  updated_at: '2026-07-01T10:15:00Z',
  balance_monitoring_enabled: false,
  balance_initial_usd: null,
  balance_remaining_usd: null,
  balance_low_threshold_usd: null,
  balance_anchor_at: null,
  balance_last_sync_at: null,
  balance_sync_status: null,
  balance_sync_error: null,
  balance_alert_level: null,
  credit_status: null,
  credit_last_probed_at: null,
  credit_probe_error: null,
};

export function makeTestAiKey(
  overrides: Partial<AiKey> & Pick<AiKey, 'id' | 'name' | 'provider'>,
): AiKey {
  return { ...AI_KEY_TEST_DEFAULTS, ...overrides };
}
