// Shared frontend helpers for the per-filament chamber-target map (#1468).
// Mirrors backend `PrintScheduler.DEFAULT_PREHEAT_FILAMENT_TARGETS`. Values
// are chamber-temperature recommendations from BambuStudio's bundled filament
// profiles; users can override the whole map via the Settings → Workflow card.

import { MAX_CHAMBER_TEMP_C } from './printer';

export const DEFAULT_PREHEAT_FILAMENT_TARGETS: Record<string, number> = {
  PLA: 0,
  PETG: 0,
  'PETG-CF': 40,
  ABS: 45,
  ASA: 45,
  PA: 50,
  'PA-CF': 55,
  PC: 50,
  'PC-FR': 50,
  TPU: 0,
  PVA: 0,
  default: 0,
};

export type ChamberProfileMode = 'off' | 'heat' | 'cool';
export interface ChamberProfile {
  mode: ChamberProfileMode;
  target: number;
}

export const DEFAULT_PREHEAT_FILAMENT_PROFILES: Record<string, ChamberProfile> = Object.fromEntries(
  Object.entries(DEFAULT_PREHEAT_FILAMENT_TARGETS).map(([key, target]) => [
    key,
    { mode: target > 0 ? 'heat' : 'off', target },
  ]),
);

// Stable display order for the editor and any preview UI. Matches the order
// users will most likely tune (engineering filaments first, then commodity).
export const PREHEAT_FILAMENT_ORDER: readonly string[] = [
  'PA-CF',
  'PA',
  'PC',
  'PC-FR',
  'ABS',
  'ASA',
  'PETG-CF',
  'PETG',
  'PLA',
  'TPU',
  'PVA',
  'default',
];

export function parsePreheatFilamentTargets(raw: string): Record<string, number> {
  if (!raw) return { ...DEFAULT_PREHEAT_FILAMENT_TARGETS };
  try {
    const parsed = JSON.parse(raw);
    if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
      const out: Record<string, number> = {};
      for (const [key, value] of Object.entries(parsed)) {
        const num = typeof value === 'number' ? value : Number(value);
        if (Number.isFinite(num)) {
          out[key] = Math.max(0, Math.min(MAX_CHAMBER_TEMP_C, Math.round(num)));
        }
      }
      if (out.default === undefined) out.default = DEFAULT_PREHEAT_FILAMENT_TARGETS.default;
      return out;
    }
  } catch {
    // fall through to defaults
  }
  return { ...DEFAULT_PREHEAT_FILAMENT_TARGETS };
}

export function serializePreheatFilamentTargets(map: Record<string, number>): string {
  // Re-encode in the canonical display order so the on-disk JSON stays
  // diff-stable. Strip 0 entries that match the default to keep the payload
  // small — except `default` itself, which we always keep for predictability.
  const out: Record<string, number> = {};
  for (const key of PREHEAT_FILAMENT_ORDER) {
    if (map[key] !== undefined) out[key] = map[key];
  }
  // Preserve any custom keys the user added that aren't in our canonical order.
  for (const [key, value] of Object.entries(map)) {
    if (!(key in out)) out[key] = value;
  }
  return JSON.stringify(out);
}

export function parsePreheatFilamentProfiles(raw: string): Record<string, ChamberProfile> {
  if (!raw) return structuredClone(DEFAULT_PREHEAT_FILAMENT_PROFILES);
  try {
    const parsed = JSON.parse(raw);
    if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
      const out: Record<string, ChamberProfile> = {};
      for (const [key, value] of Object.entries(parsed)) {
        if (typeof value === 'number' || typeof value === 'string') {
          const target = Math.max(0, Math.min(MAX_CHAMBER_TEMP_C, Math.round(Number(value) || 0)));
          out[key] = { mode: target > 0 ? 'heat' : 'off', target };
          continue;
        }
        if (value && typeof value === 'object') {
          const candidate = value as { mode?: unknown; target?: unknown };
          const mode = candidate.mode === 'heat' || candidate.mode === 'cool' ? candidate.mode : 'off';
          const target = mode === 'off'
            ? 0
            : Math.max(0, Math.min(MAX_CHAMBER_TEMP_C, Math.round(Number(candidate.target) || 0)));
          out[key] = { mode, target };
        }
      }
      if (!out.default) out.default = { ...DEFAULT_PREHEAT_FILAMENT_PROFILES.default };
      return out;
    }
  } catch {
    // Fall through to bundled defaults.
  }
  return structuredClone(DEFAULT_PREHEAT_FILAMENT_PROFILES);
}

export function serializePreheatFilamentProfiles(map: Record<string, ChamberProfile>): string {
  const out: Record<string, ChamberProfile> = {};
  for (const key of PREHEAT_FILAMENT_ORDER) {
    if (map[key]) out[key] = map[key];
  }
  for (const [key, profile] of Object.entries(map)) {
    if (!(key in out)) out[key] = profile;
  }
  return JSON.stringify(out);
}

// Normalize a printer-reported tray_type to a map key. Mirrors the backend
// `_normalize_filament_type` — split on space, uppercase.
export function normalizePreheatFilamentType(trayType: string): string {
  if (!trayType) return '';
  return trayType.split(/\s+/)[0].toUpperCase();
}

// Pick the max chamber target across a list of loaded tray types, falling
// back to `default` when a type isn't in the map. Returns 0 when nothing
// is loaded, which short-circuits the chamber phase at dispatch.
//
// A filled or foamed variant takes its base material's row when it has none of
// its own, matching `_derive_chamber_target`: ASA-GF is ASA and wants ASA's 45
// degrees, not the 0 an unmapped type falls to. The full type is still tried
// first, so PETG-CF and PA-CF keep their own hotter rows (#2902).
export function deriveChamberTargetForTrays(
  trayTypes: readonly string[],
  map: Record<string, number>,
): number {
  let best = 0;
  for (const raw of trayTypes) {
    const normalized = normalizePreheatFilamentType(raw);
    if (!normalized) continue;
    const target = map[normalized] ?? map[normalized.split('-')[0]] ?? map.default ?? 0;
    if (target > best) best = target;
  }
  return best;
}
