import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import nunjucks from 'nunjucks';

const root = path.resolve(import.meta.dirname, '..', '..');
const read = (rel) => readFileSync(path.join(root, rel), 'utf8');
const macro = read('templates/components/avatar.njk');
const body = read('templates/dashboard/account_profile_body.njk');

const env = nunjucks.configure(path.join(root, 'templates'), { autoescape: true });
const renderAvatar = (user) => env.renderString(
  '{% from "components/avatar.njk" import avatar %}{{ avatar(user, 32) }}',
  { user },
);
const initialsOf = (html) => {
  const match = html.match(/kpi-avatar__initials[^>]*>([^<]*)</);
  expect(match, `no initials span in ${html}`).not.toBeNull();
  return match[1];
};

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

describe('avatar initials (topbar "?" fix)', () => {
  it('derives initials from the display name when no initials field exists', () => {
    // The topbar passes user_avatar, which carries a display name but
    // no initials — before, the macro fell back to "?" for it.
    expect(initialsOf(renderAvatar({ display_name: 'Super Admin', avatar_url: null, initials: null }))).toBe('SA');
    expect(initialsOf(renderAvatar({ display_name: 'Zenn', avatar_url: null, initials: null }))).toBe('Z');
  });

  it('keeps an explicit initials field and falls back through the name chain', () => {
    expect(initialsOf(renderAvatar({ display_name: 'Thae Aung', initials: 'TA' }))).toBe('TA');
    expect(initialsOf(renderAvatar({ full_name: 'Aung Thu' }))).toBe('AT');
    expect(initialsOf(renderAvatar({ name: 'May Thu' }))).toBe('MT');
  });

  it('shows "?" only when there is no name at all', () => {
    expect(initialsOf(renderAvatar({}))).toBe('?');
    expect(initialsOf(renderAvatar(null))).toBe('?');
    expect(initialsOf(renderAvatar({ display_name: '' }))).toBe('?');
  });

  it('uses the first and last words and tolerates padded or doubled spaces', () => {
    // First + LAST, matching the canonical avatar_initials rule —
    // "Aung Thu Zaw" is "AZ" here and in the JS renderer / API, not "AT".
    expect(initialsOf(renderAvatar({ display_name: 'Aung Thu Zaw' }))).toBe('AZ');
    expect(initialsOf(renderAvatar({ display_name: '  Super Admin  ' }))).toBe('SA');
    // A doubled space leaves empty words in the middle, but the first
    // and last words are never empty after trim, so it still derives.
    expect(initialsOf(renderAvatar({ display_name: 'Super  Admin' }))).toBe('SA');
    expect(initialsOf(renderAvatar({ display_name: 'Super Admin', initials: '' }))).toBe('SA');
  });
});
