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
export type Me = { id: string; email: string; display_name: string };
export type UpdateT = { id: string; body: string; author: string; created_at: string };
export type QueueEntry = { membership_id: string; display_name: string; email: string };
export type AuditEntry = { id: string; action: string; actor_id: string | null; target_id: string | null; detail: string | null; created_at: string };

export const CATEGORIES: [string, string][] = [
  ["healing", "Healing"], ["grief", "Grief"], ["anxiety", "Anxiety"],
  ["provision", "Provision"], ["salvation", "Salvation"], ["family", "Family"],
  ["guidance", "Guidance"], ["travel", "Travel"], ["strength", "Strength"],
  ["thanksgiving", "Thanksgiving"], ["general", "General"],
];
