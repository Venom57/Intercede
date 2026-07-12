export type Verse = { reference: string; text: string; translation: string };

export type Req = {
  id: string; title: string; body: string; category: string; status: string;
  privacy: string; is_urgent: boolean; prayer_count: number;
  subject_type: string; subject_id: string;
  created_by: string; prayed_today: boolean; verse: Verse | null;
};

export type MemberT = { id: string; display_name: string; relationship_label: string | null; requests: Req[] };
export type FamilyT = { id: string; family_name: string; requests: Req[]; members: MemberT[] };
export type Wall = { urgent: Req[]; families: FamilyT[] };
export type GroupT = { id: string; name: string; role: string; invite_code: string | null };
export type Me = { id: string; email: string; display_name: string; is_site_admin: boolean };
export type UpdateT = { id: string; body: string; author: string; created_at: string };
export type QueueEntry = { membership_id: string; display_name: string; email: string };
export type AuditEntry = { id: string; action: string; actor_id: string | null; target_id: string | null; detail: string | null; created_at: string };
export type MemberRow = { user_id: string; membership_id: string; display_name: string; email: string; role: string; status: string };
export type AdminGroup = { id: string; name: string; created_at: string; approval_required: boolean; member_count: number; leaders: string[] };
export type AdminUser = { id: string; email: string; display_name: string; is_site_admin: boolean; created_at: string };
export type Overview = { users: number; groups: number; families: number; requests: number; prayers: number };

export const CATEGORIES: [string, string][] = [
  ["healing", "Healing"], ["grief", "Grief"], ["anxiety", "Anxiety"],
  ["provision", "Provision"], ["salvation", "Salvation"], ["family", "Family"],
  ["guidance", "Guidance"], ["travel", "Travel"], ["strength", "Strength"],
  ["thanksgiving", "Thanksgiving"], ["general", "General"],
];
