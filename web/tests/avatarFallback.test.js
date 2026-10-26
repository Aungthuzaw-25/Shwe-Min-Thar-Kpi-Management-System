import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import path from 'node:path';

const root = path.resolve(import.meta.dirname, '..', '..');
const read = (rel) => readFileSync(path.join(root, rel), 'utf8');
const macro = read('templates/components/avatar.njk');
const body = read('templates/dashboard/account_profile_body.njk');

describe('avatar fallback (photo that fails to load)', () => {
  it('keeps the per-user colour class on photo avatars too', () => {
    // Before: the colour class lived in the {% else %} branch, so a
    // photo avatar had a transparent circle — when the photo failed,
    // the white initials vanished against the white card below the
    // dark banner and looked half-cut at the junction.
    expect(macro).toContain('kpi-avatar--color-{{ color_index }}{% if image_url %} kpi-avatar--photo{% endif %}');
    expect(macro).not.toContain('{% else %} kpi-avatar--color-');
  });

  it('anchors the camera button to the avatar, not the identity block', () => {
    // Before: the button was a sibling of the avatar div, absolutely
    // positioned against .kp-summary-identity, so it floated beside
    // the role pill instead of on the avatar corner.
    const avatarCall = body.indexOf('avatar(account_profile, 128)');
    const firstClose = body.indexOf('</div>', avatarCall);
    expect(body.slice(avatarCall, firstClose)).toContain('kp-camera-button');
    expect(body.slice(avatarCall, firstClose)).toContain('data-photo-trigger');
  });
});
