// Documentation renderer only: no app build step, network request or deployment.
import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { epics, revision, evidenceTime } from './backlog-source.mjs';
const here = path.dirname(fileURLToPath(import.meta.url));
const generated = new Intl.DateTimeFormat('en-US', {timeZone: 'America/Chicago',
  dateStyle:'long', timeStyle:'short'}).format(new Date()) + ' America/Chicago';
const esc = s => String(s).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;');
const ids = new Set();
for (const epic of epics) {
  if (ids.has(epic.id)) throw Error('Duplicate epic'); ids.add(epic.id);
  for (const dep of epic.depends_on) if (!epics.some(e=>e.id===dep)) throw Error(`Unknown epic ${dep}`);
  for (const s of epic.stories) {
    if (ids.has(s.id) || !epic.features[s.feature] || !s.tasks.length || !s.acceptance.length || !s.tests.length) throw Error(`Incomplete story ${s.id}`);
    ids.add(s.id);
    for (const t of s.tasks) {if (ids.has(t.id)) throw Error('Duplicate task'); ids.add(t.id);}
    for (const a of s.acceptance) {if (ids.has(a.id) || !s.tests.includes(a.test)) throw Error('Invalid acceptance mapping'); ids.add(a.id);}
  }
}
const visiting=new Set(), visited=new Set();
function visit(e) {if(visiting.has(e.id)) throw Error('Dependency cycle'); if(visited.has(e.id)) return;
  visiting.add(e.id); for(const dep of e.depends_on) visit(epics.find(x=>x.id===dep)); visiting.delete(e.id); visited.add(e.id);}
epics.forEach(visit);
const stories = epics.flatMap(e=>e.stories);
const storyMap = new Map(stories.map(s=>[s.id,s]));
visiting.clear(); visited.clear();
function visitStory(s) {if(visiting.has(s.id)) throw Error('Story dependency cycle'); if(visited.has(s.id)) return;
  visiting.add(s.id); for(const dep of s.depends_on) {if(!storyMap.has(dep)) throw Error('Unknown story '+dep); visitStory(storyMap.get(dep));} visiting.delete(s.id); visited.add(s.id);}
stories.forEach(visitStory);
const tasks = stories.flatMap(s=>s.tasks);
const criteria = stories.flatMap(s=>s.acceptance);
let md = `# SelfLearning delivery backlog\n\n${revision}. Evidence: ${evidenceTime}. Export: ${generated}.\n\n`;
md += `${epics.length} epics, ${Object.values(epics).reduce((n,e)=>n+Object.keys(e.features).length,0)} features, ${stories.length} user stories, ${tasks.length} tasks and ${criteria.length} acceptance criteria. Test identifiers below are required coverage, not a claim that every named test exists or passes. No production authorization is implied.\n\n`;
md += 'Use one file-owned task brief per implementation partition. Story-level prerequisites override aggregate epic integration dependencies: R130/S012 and existing eligibility/S016 are independent NFL correctness work. S028 defines the release process; each release manifest must bind every included story and its acceptance evidence, not assume all milestones are complete. Independent QA verifies exact revisions. See TECHNICAL_PLAN.md for assumptions, current evidence, rollout and timing.\n\n';
const csv = [['Epic','Feature','Milestone','Owner','Story','Title','User story','Status','Effort','Risk','Depends on epics','Depends on stories','Task','Task action','Acceptance and required tests']];
for (const e of epics) {
  md += `## ${e.id} ${e.title}\n\nMilestone: ${e.milestone}. Owner: ${e.owner}. Depends on: ${e.depends_on.join(', ') || 'none'}.\n\n`;
  md += `Features: ${Object.entries(e.features).map(([id,title])=>`${e.id}-${id} ${title}`).join('; ')}.\n\n`;
  for (const s of e.stories) {
    md += `### ${s.id} ${s.title}\n\nFeature: ${e.id}-${s.feature}. Status: ${s.status}. Effort: ${s.effort}. Risk: ${s.risk}. Prerequisite stories: ${s.depends_on.join(', ')||'none; independently deliverable'}.\n\n${s.user_story}\n\nTasks:\n\n`;
    md += s.tasks.map(t=>`- ${t.id}: ${t.action}`).join('\n')+'\n\nAcceptance and required evidence:\n\n';
    md += s.acceptance.map(a=>`- ${a.id}: ${a.criterion} Required test: ${a.test}.`).join('\n')+'\n\n';
    for(const t of s.tasks) csv.push([e.id,`${e.id}-${s.feature}`,e.milestone,e.owner,s.id,s.title,s.user_story,s.status,s.effort,s.risk,e.depends_on.join(';'),s.depends_on.join(';'),t.id,t.action,s.acceptance.map(a=>`${a.id}: ${a.criterion} [${a.test}]`).join('\n')]);
  }
}
await fs.writeFile(path.join(here,'BACKLOG.md'),md.trimEnd()+'\n');
await fs.writeFile(path.join(here,'BACKLOG.csv'),csv.map(row=>row.map(v=>'"'+String(v).replaceAll('"','""')+'"').join(',')).join('\n')+'\n');

const box=(x,y,w,h,title,lines=[],kind='proposed')=>`<g><rect x="${x}" y="${y}" width="${w}" height="${h}" rx="12" class="${kind}"/><text x="${x+14}" y="${y+26}" class="bt">${esc(title)}</text>${lines.map((t,i)=>`<text x="${x+14}" y="${y+49+i*21}" class="bl">${esc(t)}</text>`).join('')}</g>`;
const arrow=(x1,y1,x2,y2,label='',lx=(x1+x2)/2,ly=(y1+y2)/2-9)=>`<path d="M${x1},${y1} L${x2},${y2}" class="arrow" marker-end="url(#tip)"/>${label?`<text x="${lx}" y="${ly}" class="al" text-anchor="middle">${esc(label)}</text>`:''}`;
const svg=(content,h=460)=>`<svg viewBox="0 0 1050 ${h}" role="img" xmlns="http://www.w3.org/2000/svg"><defs><marker id="tip" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0 0 L10 5 L0 10Z" fill="#31556c"/></marker></defs>${content}</svg>`;
const panel=(title,body,kind='')=>`<article class="panel ${kind}"><h3>${title}</h3>${body}</article>`;
const list=items=>`<ul>${items.map(x=>`<li>${x}</li>`).join('')}</ul>`;
const page=(n,title,subtitle,body)=>`<section class="page" id="page-${n}"><header><div class="eyebrow">J5L · SelfLearning and NFL2026 · ${revision}</div><h1>${title}</h1><p class="subtitle">${subtitle}</p></header><main>${body}</main><footer><span>${revision} · ${esc(generated)} · Proposed design unless marked verified</span><span>${n} / 7</span></footer></section>`;
const pages=[];
pages.push(page(1,'One learning engine, separate serving authority','Purpose and boundaries: preserve what was predicted, learn from trustworthy outcomes, and improve only with evidence.',
 `<div class="legend"><span class="verified">Verified current foundation</span><span class="proposed">Proposed integration</span><span class="warning">Incomplete or requires approval</span></div>`+
 svg(
 box(10,35,210,112,'Source evidence',['Schedule, stats, injuries','Weather vintages: planned','Markets: NFL benchmark only'],'verified')+
 box(275,35,225,112,'NFL2026 producer',['Netlify PWA + Actions','Owns offers and predictions','Approved model artifacts'],'verified')+
 box(570,35,225,112,'Shared API on Vercel',['Existing local FastAPI work','Auth + adapter validation','Hosted integration unverified'])+
 box(840,35,200,112,'Supabase journal',['4 foundation tables','Project verified via MCP','New evidence model planned'],'verified')+
 arrow(220,91,275,91,'as-of',247,76)+arrow(500,91,570,91,'receipts',535,76)+arrow(795,91,840,91,'commit',817,76)+
 box(570,255,225,112,'Evaluation job',['Bounded Python Actions','Frozen cohorts + FINAL only','Returns shadow candidates'])+
 box(275,255,225,112,'NFL adoption gate',['Compatibility + own evidence','Owner approval + artifact pin','New forecasts only'],'warning')+
 box(10,255,210,112,'Owner / chat / dashboard',['Status, traces, uploads','Same authenticated API','No database-owner access'])+
 arrow(914,147,914,220,'pinned records',915,191)+arrow(914,220,682,220)+arrow(682,220,682,255)+
 arrow(570,312,500,312,'candidate',535,297)+arrow(387,255,387,147,'approved',425,209)+
 arrow(220,312,275,312,'review',247,297),400)+
 `<div class="cols two">${panel('Current truth','<p>Operator token is configured per owner. Supabase management access and foundation tables are verified. Neither proves a working NFL feedback loop.</p>')}${panel('Non-goals','<p>No bets or trades, no automatic promotion, no guaranteed parlay wins, no new frontend framework, and no reconstruction of missing historical evidence.</p>')}</div>`));
pages.push(page(2,'Components, contracts and persistence','Mid-level design: the database owns durable observations; NFL owns the contract users saw and what serves next.',
 svg(
 box(15,20,255,120,'NFL immutable outbox',['Envelope + source snapshots','Before display-card dedupe','Generation + selection versions'])+
 box(380,20,285,120,'API authentication boundary',['Server-derived owner + task','Separate ingest / evaluator scopes','Validate; acknowledge after commit'])+
 box(775,20,260,120,'Supabase persistent authority',['Append-only receipts + revisions','DB-owned received timestamps','Private files; explicit grants'])+
 arrow(270,78,380,78,'versioned batch',325,61)+arrow(665,78,775,78,'transaction',720,61)+
 box(15,235,255,135,'Source adapter',['NFL: IDs, eligibility, yards','Other domains: own policy','No sportsbook predictors in NFL'])+
 box(380,235,285,135,'Domain-neutral core',['Identity, lineage, revisions','Cohorts, scores, grouped evidence','No NFL injury/weather rules'])+
 box(775,235,260,135,'Operator and intake',['Dashboard + application MCP','Draft parsing and confirmation','Read-only tools by default'])+
 arrow(270,295,380,295,'validated envelope',325,278)+arrow(907,235,907,210,'auth API',950,228)+arrow(907,210,610,210)+arrow(610,210,610,140)+
 arrow(522,140,522,235,'durable records',580,196),390)+
 `<div class="cols three">${panel('Forecast identity','<p>Contract ID ≠ display card ID ≠ source receipt ID ≠ persisted envelope ID. Preserve each join explicitly.</p>')}${panel('Original versus correction','<p>New outcome revisions supersede by chain. Old forecasts and evaluation manifests remain unchanged.</p>')}${panel('Production state','<p>Vercel filesystem and local SQLite are not the hosted store. Supabase schema/API extensions still need build and approval.</p>')}</div>`));
pages.push(page(3,'Forecast, publication and outcome pipelines','Detailed control flow: freeze evidence before results; distinguish internal capture from observed public availability.',
 `<div class="pipeline"><h3>Forecast and publication</h3><div class="steps">${['Fetch as-of sources','Generate base → serve once','Journal before dedupe','Gate exact generation','Publish + verify hashes','Export + durable ACK'].map((x,i)=>`<div><b>${i+1}</b>${x}</div>`).join('')}</div></div>`+
 `<div class="cols two">${panel('Failure path before publication',list(['Unknown status → quarantine its identifiable row.','Stale source or hash mismatch → reject with reason.','Keep R102 checks and R106 archive protection.']))}${panel('Failure path after publication',list(['Engine down → preserve outbox; retry same IDs.','Conflicting payload → reject; never overwrite.','ACK only after commit; alert before kickoff.']))}</div>`+
 `<div class="pipeline"><h3>Outcome and learning</h3><div class="steps">${['Authoritative FINAL','Append revision chain','Pin exact cohort/revisions','Chronological evaluation','Shadow candidate','NFL gate + owner review'].map((x,i)=>`<div><b>${i+1}</b>${x}</div>`).join('')}</div></div>`+
 `<div class="callout"><b>Three clocks:</b> source availability → forecast creation → internal/database observation. A later import cannot prove earlier capture. Public availability needs a separate deployment/content-hash observation.</div>`+
 `<div class="cols two">${panel('Sampling boundary','<p>Pool rungs, MY offers and Game/Week cards are distinct populations. Compare the same declared cohort; copies are not independent samples.</p>')}${panel('Scheduling and recovery','<p>Actions completion + manual dispatch; cron is best effort. Durable leases/checkpoints recover repeated or killed jobs.</p>')}</div>`));
pages.push(page(4,'Learning and adoption states','Statistical and serving authority stay separate. Uploading evidence or fitting a model is not permission to use it.',
 svg(
 box(15,25,225,103,'Generated receipt',['Frozen base + served value','Public status unverified'])+
 box(300,25,225,103,'Durably captured',['Validate exact source receipt','Immutable first observation'])+
 box(585,25,225,103,'FINAL resolution',['Exact persisted envelope join','Append correction revisions'])+
 arrow(240,77,300,77,'ingest',270,61)+arrow(525,77,585,77,'resolve',555,61)+
 box(585,233,225,105,'Shadow candidate',['Base-compatible typed artifact','Train before held-out events'])+
 box(300,233,225,105,'Owner review',['Common-cohort evidence','Subgroups + clustered interval'],'warning')+
 box(15,233,225,105,'Approved future serving',['NFL compatibility gate','Pinned artifact, once only'],'warning')+
 arrow(697,128,697,233,'evaluate',739,188)+arrow(585,285,525,285,'review',555,269)+arrow(300,285,240,285,'approve',270,269),355)+
 `<div class="cols two">${panel('Equation contract','<p>Generic Platt: sigmoid(a × confidence + b). NFL: sigmoid(a + b × logit(<b>immutable base</b>)), then clamp and round. These are not interchangeable.</p>')}${panel('Timing and rollback','<p>Current MY policy needs ≥100 legs and ≥2 held-out week folds: normally ≥3 receipt weeks. R127 separately needs ≥2 new graded weeks. Rollback changes future serving, not history.</p>')}</div>`+
 `<div class="callout">Do not refit to Week 5 or one TNF result. Candidate population, base model, artifact hash, evaluation revisions and effective version must all remain traceable.</div>`));
pages.push(page(5,'Operator and chat journeys','User action → implementation → visible result → observable acceptance. Mobile dashboard is the fallback if chat cannot handle images.',
 `<div class="journeys"><div class="journey"><h3>1 · Is learning actually connected?</h3><div class="journeyline"><span>Open status</span><i>→</i><span>Authenticated API + DB check</span><i>→</i><span>SHA, durable ingest, job age</span><i>→</i><span>UI-01 / OPS-03</span></div><p>Empty, stale and unauthorized states have recovery guidance. MCP connected alone never displays “learning active”.</p></div>`+
 `<div class="journey"><h3>2 · Why this prediction or losing parlay?</h3><div class="journeyline"><span>Select receipt/card</span><i>→</i><span>Canonical trace + cohort</span><i>→</i><span>Source, versions, exposure</span><i>→</i><span>EVAL-01 / PORTFOLIO-02</span></div><p>Show generated/public, pending/FINAL, original/reduced settlement and shared-loss blast radius without changing the original bet.</p></div>`+
 `<div class="journey"><h3>3 · Analyze my screenshot</h3><div class="journeyline"><span>Private upload</span><i>→</i><span>Draft parse + confirm IDs</span><i>→</i><span>Joined ticket + uncertainty</span><i>→</i><span>INTAKE-01/02/03</span></div><p>Ambiguous game/player/line stays unresolved. Parsed text is untrusted; it cannot create verified publication or a FINAL label.</p></div>`+
 `<div class="journey"><h3>4 · Should we use this improvement?</h3><div class="journeyline"><span>Compare candidate</span><i>→</i><span>Pinned held-out evaluation</span><i>→</i><span>Hold reason or approval review</span><i>→</i><span>PROMOTE-01 / RETURN-02</span></div><p>Read-only MCP tools inspect evidence. Approval is a separate authorized action; active version means consumed by NFL, not latest proposed.</p></div></div>`+
 `<div class="callout"><b>Questions are not automatically training data.</b> Objective predictions need a defined horizon and authoritative outcome. Subjective approval stays review feedback, not truth.</div>`));
pages.push(page(6,'Parallel team and implementation roadmap','Four execution slots in this session. Speed comes from disjoint ownership and frozen contracts, not skipping evidence or review.',
 `<div class="cols four">${panel('Coordinator','<p>Architecture, contract ownership, integration manifest, one owner question when needed.</p>')}${panel('NFL lane','<p>Eligibility, generation journal, publisher gate, exporter and approved consumer.</p>')}${panel('Engine lane','<p>Adapters, authenticated API, durable store, revisions and migration proposal.</p>')}${panel('Evaluation / UX lane','<p>Cohorts, replay, metrics and operator views; independent QA rotates into a freed slot.</p>')}</div>`+
 `<div class="roadmap"><div><b>M0 · Foundation</b><span>Reproducible source, schema and scoped credentials</span><em>1–2 engineer-days</em></div><div><b>M1 · Prospective capture</b><span>Journal before dedupe, exact generation gate</span><em>2–3 days</em></div><div><b>M2 · Durable shadow loop</b><span>API, outbox/ACK, persistence, FINAL revisions</span><em>3–4 days</em></div><div><b>M3 · Evaluation and return</b><span>Parity, typed artifact, independent NFL gates</span><em>3–5 days</em></div><div><b>M4 · Operator and NFL work</b><span>R130, portfolio, R127/weather shadow, MCP/status</span><em>2–4 days; overlaps</em></div><div><b>M5 / M6 · Adopt then extend</b><span>Evidence + owner approval; one domain pilot at a time</span><em>Observation time extra</em></div></div>`+
 `<div class="callout">Supported MY shadow-to-controlled-return build: roughly <b>10–18 engineer-days with overlap</b>. Broad screenshot understanding and real financial/F1/soccer/NCAA/election adapters are additional scope. No same-day accuracy promise.</div>`+
 `<p class="small">Claude Code is the proposed release coordinator per NFL workflow, not a live connected agent here. Exact SHA handoffs and independent tests connect the sessions. Owner authorizes production migrations, deploys and activation.</p>`));
pages.push(page(7,'Acceptance gates and the owner’s next actions','The architecture becomes real only when the complete round trip is demonstrated on exact, reproducible revisions.',
 `<div class="cols two">${panel('Required end-to-end evidence',list(['Pending receipt captured before kickoff, independently of display IDs.','Commit ACK → exact retry no-op → conflict rejected.','FINAL correction linked to the exact persisted envelope.','NFL/engine identical cohort and served-score parity.','Earlier-base fit → shadow candidate → incompatible return rejected.','Approved future artifact applied once → second cycle → outage/rollback.']))}${panel('Your remaining actions',list(['No new operator-token setup; keep secret values out of chat.','If convenient, confirm SUPABASE_URL and a supported backend secret key are configured; they may already exist.','Approve reviewed migration and release only after test evidence.','Later choose portfolio tradeoff and any candidate activation.','No new paid provider or automatic promotion is needed to start.']))}</div>`+
 `<div class="cols three">${panel('Current verified foundation','<p>Supabase project + 4 RLS-enabled tables; migration history and advisor results. Operator token present per owner.</p>')}${panel('Current limits','<p>Hosted end-to-end auth/DB round trip unverified. Helper grants need review; migration IDs differ. Remote checks are restricted here.</p>')}${panel('Local implementation evidence','<p>Producer-parity and journal tests passed locally. This is not hosted persistence, production ingestion, candidate adoption or proven accuracy gain.</p>')}</div>`+
 `<div class="callout"><b>Source package:</b> TECHNICAL_PLAN.md · BACKLOG.md / CSV · backlog-source.mjs · render.mjs · editable architecture.html. ${epics.length} epics · ${stories.length} stories · ${tasks.length} tasks · ${criteria.length} acceptance criteria.</div>`+
 `<p class="small">Primary references: vercel.com/docs/frameworks/backend/fastapi · supabase.com/docs/guides/api/securing-your-api · GitHub scheduled-workflow docs. Technical plan links exact source baselines and evidence.</p>`));
const css=`*{box-sizing:border-box}body{margin:0;background:#e6ebef;color:#173044;font:15px/1.45 Arial,sans-serif}.page{width:1122px;height:793px;padding:27px 36px 45px;background:white;position:relative;page-break-after:always;overflow:hidden}header{height:112px}h1{font-size:29px;line-height:1.15;letter-spacing:-.5px;margin:7px 0 7px}h3{font-size:16px;line-height:1.2;margin:0 0 9px;color:#143448}p{margin:0 0 9px}.eyebrow{font-size:12px;letter-spacing:1.3px;color:#317c83;font-weight:bold;text-transform:uppercase}.subtitle{font-size:16px;color:#506573;max-width:1010px}main{height:594px}footer{position:absolute;bottom:17px;left:36px;right:36px;display:flex;justify-content:space-between;border-top:1px solid #ccd7df;padding-top:7px;font-size:11px;color:#5b6e7c}.cols{display:grid;gap:15px;margin:10px 0}.two{grid-template-columns:1fr 1fr}.three{grid-template-columns:repeat(3,1fr)}.four{grid-template-columns:repeat(4,1fr)}.panel{border:1px solid #c9d8df;border-top:4px solid #35868b;background:#f6f9fb;border-radius:8px;padding:13px 15px}.panel p,.panel li{font-size:14px}.panel p:last-child{margin-bottom:0}ul{padding-left:18px;margin:0}li{margin:0 0 8px}.legend{display:flex;gap:14px;margin-bottom:2px}.legend span{border-radius:5px;border:1px solid;padding:4px 10px;font-size:12px}.verified{fill:#e9f4ef;stroke:#43856c;background:#e9f4ef;border-color:#43856c}.proposed{fill:#edf3fa;stroke:#47718e;background:#edf3fa;border-color:#47718e}.warning{fill:#fff5df;stroke:#be8a2d;background:#fff5df;border-color:#be8a2d}svg{display:block;width:100%;height:auto;max-height:410px}svg rect{stroke-width:1.6}.bt{font-size:16px;font-weight:bold;fill:#183448}.bl{font-size:14px;fill:#284859}.arrow{stroke:#31556c;stroke-width:2;fill:none}.al{font-size:12px;fill:#365867;font-weight:bold;paint-order:stroke;stroke:white;stroke-width:5px;stroke-linejoin:round}.callout{padding:13px 16px;background:#eaf4f5;border-left:5px solid #2f8188;border-radius:4px;margin:13px 0;font-size:14px}.pipeline{margin:5px 0 13px;padding:12px;background:#f1f5f8;border-radius:8px}.steps{display:grid;grid-template-columns:repeat(6,1fr);gap:11px}.steps>div{font-size:14px;border:1px solid #c8d5dd;background:white;border-radius:7px;padding:10px;min-height:76px;position:relative}.steps>div:not(:last-child):after{content:'›';position:absolute;right:-11px;top:21px;font-size:24px;color:#367b86}.steps b{display:block;color:#367b86;margin-bottom:4px}.journey{border-left:4px solid #367f87;background:#f4f8fa;padding:12px 16px;margin:0 0 12px}.journey h3{margin-bottom:8px}.journey p{font-size:13px;margin:6px 0 0}.journeyline{display:flex;align-items:center;gap:12px;font-size:14px}.journeyline span{flex:1}.journeyline i{font-style:normal;color:#367f87}.roadmap{display:grid;gap:7px;margin-top:16px}.roadmap>div{display:grid;grid-template-columns:215px 1fr 195px;gap:12px;align-items:center;border:1px solid #d1dce3;border-left:5px solid #3a7f86;border-radius:5px;padding:10px 12px;font-size:14px}.roadmap em{font-style:normal;color:#526e7f;text-align:right}.small{font-size:12px;color:#546b7c}@media screen{.page{margin:20px auto;box-shadow:0 4px 20px #19374620}}@media print{@page{size:1122px 793px;margin:0}body{background:white}.page{margin:0;box-shadow:none}.page:last-child{page-break-after:auto}}`;
const html=`<!doctype html><html lang="en"><head><meta charset="utf-8"><title>SelfLearning architecture ${revision}</title><style>${css}</style></head><body>${pages.join('')}</body></html>`;
await fs.writeFile(path.join(here,'architecture.html'),html);
const summary={revision,evidenceTime,generated,epics:epics.length,features:epics.reduce((n,e)=>n+Object.keys(e.features).length,0),stories:stories.length,tasks:tasks.length,acceptance_criteria:criteria.length,dependency_dag_valid:true};
await fs.writeFile(path.join(here,'backlog-manifest.json'),JSON.stringify(summary,null,2)+'\n');
console.log(JSON.stringify(summary));
if (!process.argv.includes('--pdf')) process.exit(0);
const modulePath=process.env.PLAYWRIGHT_MODULE;
if(!modulePath) throw Error('Set PLAYWRIGHT_MODULE to an existing pinned Playwright index.mjs; documentation rendering only.');
const {chromium}=await import(pathToFileURL(modulePath).href);
const browser=await chromium.launch({executablePath:process.env.PW_CHROMIUM || undefined,headless:true});
try {
  const tab=await browser.newPage({viewport:{width:1200,height:900},deviceScaleFactor:1});
  await tab.goto(pathToFileURL(path.join(here,'architecture.html')).href);
  await tab.emulateMedia({media:'print'});
  const geometry=await tab.locator('.page').evaluateAll(nodes=>nodes.map(n=>({id:n.id,width:n.clientWidth,height:n.clientHeight,scrollHeight:n.scrollHeight,
    outOfBounds:[...n.querySelectorAll('main *')].filter(x=>{let r=x.getBoundingClientRect(),p=n.getBoundingClientRect();return r.width>0 && (r.right>p.right+1 || r.bottom>p.bottom-35 || r.left<p.left-1);}).map(x=>x.tagName)})));
  if(geometry.some(g=>g.scrollHeight>g.height||g.outOfBounds.length)) throw Error('Layout overflow: '+JSON.stringify(geometry));
  await fs.mkdir(path.join(here,'previews'),{recursive:true});
  for(let i=1;i<=pages.length;i++) await tab.locator(`#page-${i}`).screenshot({path:path.join(here,'previews',`page-${i}.png`)});
  await tab.pdf({path:path.join(here,'SelfLearning_Architecture_R1.pdf'),width:'1122px',height:'793px',printBackground:true,preferCSSPageSize:true});
  await fs.writeFile(path.join(here,'render-verification.json'),JSON.stringify({generated,pages:pages.length,geometry,pdf:'SelfLearning_Architecture_R1.pdf'},null,2)+'\n');
  console.log('Rendered PDF and all7page previews; geometry checks passed.');
} finally {await browser.close();}
