/* OpenWorlds initial client state.

   This is a CLEAN, EMPTY seed — it intentionally ships NO campaign, party,
   quest, item, location, or table-log content. Every live screen binds its own
   engine read-model ("surface") fetched from the viewer server; `campaigns` is
   replaced on mount by the live `/openworlds/campaigns.json` catalog.

   Do NOT add demo/sample game content here: any value a screen reads off
   `state.*` (party / quests / stash / locations / tableLog) leaks directly into
   a live session. Screens must render a graceful empty-state when their surface
   is absent or empty — never a hardcoded fallback. */

const INITIAL_STATE = {
  activeCampaign: "",
  campaigns: [],
  party: [],
  stash: [],
  quests: [],
  locations: [],
  tableLog: [],
};

window.INITIAL_STATE = INITIAL_STATE;
