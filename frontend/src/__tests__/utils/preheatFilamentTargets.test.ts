import { describe, expect, it } from 'vitest';

import {
  parsePreheatFilamentProfiles,
  serializePreheatFilamentProfiles,
} from '../../utils/preheatFilamentTargets';

describe('preheat filament chamber profiles', () => {
  it('keeps legacy numeric targets as heat profiles', () => {
    const profiles = parsePreheatFilamentProfiles('{"PETG":45,"PLA":0}');

    expect(profiles.PETG).toEqual({ mode: 'heat', target: 45 });
    expect(profiles.PLA).toEqual({ mode: 'off', target: 0 });
  });

  it('parses and serializes heat, cool, and off profiles', () => {
    const profiles = parsePreheatFilamentProfiles(
      '{"PETG":{"mode":"cool","target":35},"ABS":{"mode":"heat","target":50},"PLA":{"mode":"off","target":55}}',
    );

    expect(profiles.PETG).toEqual({ mode: 'cool', target: 35 });
    expect(profiles.ABS).toEqual({ mode: 'heat', target: 50 });
    expect(profiles.PLA).toEqual({ mode: 'off', target: 0 });
    expect(JSON.parse(serializePreheatFilamentProfiles(profiles))).toMatchObject(profiles);
  });

  it('falls back to bundled profiles for invalid JSON', () => {
    const profiles = parsePreheatFilamentProfiles('not-json');

    expect(profiles.default).toEqual({ mode: 'off', target: 0 });
    expect(profiles.ABS.mode).toBe('heat');
  });
});
