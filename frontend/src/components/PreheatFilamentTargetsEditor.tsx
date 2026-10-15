import { useTranslation } from 'react-i18next';
import {
  DEFAULT_PREHEAT_FILAMENT_PROFILES,
  PREHEAT_FILAMENT_ORDER,
  parsePreheatFilamentProfiles,
  serializePreheatFilamentProfiles,
  type ChamberProfileMode,
} from '../utils/preheatFilamentTargets';
import { MAX_CHAMBER_TEMP_C } from '../utils/printer';

interface Props {
  // JSON-encoded map; empty string means "use bundled defaults".
  value: string;
  onChange: (next: string) => void;
  disabled?: boolean;
}

// Per-filament chamber target editor for Settings → Workflow → Preheat card
// (#1468). Renders one row per filament type with a numeric input clamped to
// 0-MAX_CHAMBER_TEMP_C. Stripping back to the bundled defaults is handled by the parent
// (Reset button next to the section title) — passing an empty string upward
// is the canonical "use defaults" signal, which keeps the editor stateless
// across resets.
export function PreheatFilamentTargetsEditor({ value, onChange, disabled = false }: Props) {
  const { t } = useTranslation();
  const map = parsePreheatFilamentProfiles(value);

  const updateTarget = (key: string, next: number) => {
    const clamped = Math.max(0, Math.min(MAX_CHAMBER_TEMP_C, Math.round(next)));
    const current = map[key] ?? DEFAULT_PREHEAT_FILAMENT_PROFILES[key] ?? { mode: 'off', target: 0 };
    onChange(serializePreheatFilamentProfiles({ ...map, [key]: { ...current, target: clamped } }));
  };

  const updateMode = (key: string, mode: ChamberProfileMode) => {
    const current = map[key] ?? DEFAULT_PREHEAT_FILAMENT_PROFILES[key] ?? { mode: 'off', target: 0 };
    const defaultTarget = mode === 'heat' ? 40 : mode === 'cool' ? 35 : 0;
    onChange(serializePreheatFilamentProfiles({
      ...map,
      [key]: { mode, target: mode === 'off' ? 0 : current.target || defaultTarget },
    }));
  };

  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 gap-x-3 gap-y-1.5">
      {PREHEAT_FILAMENT_ORDER.map((key) => {
        const current = map[key] ?? DEFAULT_PREHEAT_FILAMENT_PROFILES[key] ?? { mode: 'off', target: 0 };
        const label = key === 'default'
          ? t('settings.preheatFilamentTargetsDefaultRow', 'Other / unmapped')
          : key;
        return (
          <div key={key} className="flex items-center justify-between gap-2">
            <span className={`text-xs ${key === 'default' ? 'text-bambu-gray italic' : 'text-bambu-gray'}`}>
              {label}
            </span>
            <div className="flex items-center gap-1">
              <select
                value={current.mode}
                onChange={(event) => updateMode(key, event.target.value as ChamberProfileMode)}
                disabled={disabled}
                className="rounded border border-bambu-dark-tertiary bg-bambu-dark px-1 py-1 text-xs text-white focus:border-bambu-green focus:outline-none disabled:opacity-50"
              >
                <option value="off">{t('common.off')}</option>
                <option value="heat">{t('externalChamberCooling.heat')}</option>
                <option value="cool">{t('externalChamberCooling.cool')}</option>
              </select>
              <input
                type="number"
                max={MAX_CHAMBER_TEMP_C}
                step={1}
                value={current.target}
                min={current.mode === 'cool' ? 20 : current.mode === 'heat' ? 40 : 0}
                onChange={(e) => updateTarget(key, parseInt(e.target.value, 10) || 0)}
                disabled={disabled || current.mode === 'off'}
                className="w-16 px-2 py-1 bg-bambu-dark border border-bambu-dark-tertiary rounded text-white text-xs text-right focus:outline-none focus:border-bambu-green disabled:opacity-50"
              />
              <span className="text-xs text-bambu-gray">°C</span>
            </div>
          </div>
        );
      })}
    </div>
  );
}
