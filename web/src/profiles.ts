import type { Profile } from './api';

export const profiles: { id: Profile; name: string }[] = [
  { id: 'baseline', name: 'Evidence baseline' },
  { id: 'demo-balanced', name: 'Balanced · mock' },
  { id: 'demo-fast', name: 'Fast · mock' },
  { id: 'demo-timeout', name: 'Timeout · mock' },
  { id: 'demo-malformed', name: 'Invalid response · mock' },
  { id: 'demo-rate-limited', name: 'Rate limit · mock' },
  { id: 'demo-unavailable', name: 'Unavailable · mock' },
];
