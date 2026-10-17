// Portable vector PDF export from the same editable HTML, no browser/dependencies.
// The HTML renderer can also print through Playwright where browser launch is allowed.
import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {execFileSync} from 'node:child_process';
const dir=path.dirname(fileURLToPath(import.meta.url));
const source=await fs.readFile(path.join(dir,'architecture.html'),'utf8');
const W=1122,H=793;
const colors={ink:'#173044',muted:'#506573',teal:'#317c83',line:'#c9d8df',panel:'#f6f9fb',
  verified:'#e9f4ef',proposed:'#edf3fa',warning:'#fff5df'};
const decode=s=>s.replaceAll('&amp;','&').replaceAll('&lt;','<').replaceAll('&gt;','>').replaceAll('&quot;','"');
function parse(html) {
  const root={tag:'root',attrs:{},children:[]}, stack=[root];
  for(const tok of html.match(/<[^>]*>|[^<]+/g)||[]) {
    if(tok.startsWith('</')){stack.pop();continue;}
    if(tok.startsWith('<')) {
      if(tok.startsWith('<!')) continue;
      const tag=tok.match(/^<([\w-]+)/)?.[1]; if(!tag)continue;
      const attrs=Object.fromEntries([...tok.matchAll(/([\w-]+)="([^"]*)"/g)].map(m=>[m[1],decode(m[2])]));
      const n={tag,attrs,children:[]}; stack.at(-1).children.push(n);
      if(!tok.endsWith('/>')&&!['meta','br','hr','input'].includes(tag)) stack.push(n);
    } else stack.at(-1).children.push({tag:'#text',text:decode(tok)});
  } return root;
}
const textContent=n=>n.tag==='#text'?n.text:(n.children||[]).map(textContent).join('');
const children=n=>(n.children||[]).filter(x=>x.tag!=='#text');
const find=(n,tag)=>n.tag===tag?n:children(n).map(x=>find(x,tag)).find(Boolean);
const klass=(n,c)=>(n.attrs.class||'').split(' ').includes(c);
const ascii=s=>s.replaceAll('→',' -> ').replaceAll('×',' x ').replaceAll('≥','>=')
  .replaceAll('≤','<=').replaceAll('≠','!=').replaceAll('’',"'").replaceAll('‘',"'")
  .replaceAll('“','"').replaceAll('”','"').replaceAll('–','-').replaceAll('—','-')
  .replaceAll('·',' / ').replaceAll('›','>').replaceAll('−','-').replaceAll('≈','~')
  .replace(/[^\x20-\x7e\n]/g,'?');
function width(s,size=14) {return [...ascii(s)].reduce((n,c)=>n+(" ilI.,:;'|!".includes(c)?.25:'MW@%'.includes(c)?.85:.53)*size,0);}
function wrap(s,w,size=14) {
  const words=ascii(s).trim().replace(/\s+/g,' ').split(' '); const out=[]; let line='';
  for(const word of words) {if(line&&width(line+' '+word,size)>w){out.push(line);line=word;} else line+=(line?' ':'')+word;}
  if(line)out.push(line);return out;
}
function rgb(hex){const h=hex.replace('#','');return [0,2,4].map(i=>parseInt(h.slice(i,i+2),16)/255).map(n=>n.toFixed(4)).join(' ');}
const escapePDF=s=>ascii(s).replaceAll('\\','\\\\').replaceAll('(','\\(').replaceAll(')','\\)');
let commands=[], geometry=[];
function rect(x,y,w,h,fill=colors.panel,stroke=colors.line,lw=1) {
  commands.push(`q ${rgb(fill)} rg ${rgb(stroke)} RG ${lw} w ${x} ${H-y-h} ${w} ${h} re B Q`);
}
function line(x1,y1,x2,y2,color='#31556c',lw=1.5){commands.push(`q ${rgb(color)} RG ${lw} w ${x1} ${H-y1} m ${x2} ${H-y2} l S Q`);}
function text(s,x,y,size=14,bold=false,color=colors.ink,anchor='left') {
  if(anchor==='middle')x-=width(s,size)/2;
  commands.push(`BT /${bold?'F2':'F1'} ${size} Tf ${rgb(color)} rg 1 0 0 1 ${x.toFixed(2)} ${(H-y).toFixed(2)} Tm (${escapePDF(s)}) Tj ET`);
  geometry.push({text:ascii(s),x,y,width:width(s,size),size});
}
function para(s,x,y,w,size=14,bold=false,color=colors.ink) {
  for(const t of wrap(s,w,size)){text(t,x,y+size,size,bold,color);y+=size*1.35;}return y;
}
function drawSVG(n,x,y,w) {
  const [, ,vw,vh]=n.attrs.viewBox.split(' ').map(Number),scale=w/vw;
  const px=v=>x+Number(v)*scale,py=v=>y+Number(v)*scale;
  function draw(a) {
    if(a.tag==='defs')return;
    if(a.tag==='rect'){const c=a.attrs.class;rect(px(a.attrs.x),py(a.attrs.y),a.attrs.width*scale,a.attrs.height*scale,colors[c]||colors.proposed,c==='verified'?'#43856c':c==='warning'?'#be8a2d':'#47718e',1.4);}
    if(a.tag==='text')text(textContent(a),px(a.attrs.x),py(a.attrs.y),Number(a.attrs.class==='bt'?16:a.attrs.class==='al'?12:14)*scale,a.attrs.class!=='bl',colors.ink,a.attrs['text-anchor']==='middle'?'middle':'left');
    if(a.tag==='path'&&a.attrs.class==='arrow'){
      const nums=a.attrs.d.match(/-?\d+(?:\.\d+)?/g).map(Number);line(px(nums[0]),py(nums[1]),px(nums[2]),py(nums[3]));
      if(a.attrs['marker-end']){const xx=px(nums[2]),yy=py(nums[3]),ang=Math.atan2(nums[3]-nums[1],nums[2]-nums[0]),len=8;
        line(xx,yy,xx-len*Math.cos(ang-.5),yy-len*Math.sin(ang-.5));line(xx,yy,xx-len*Math.cos(ang+.5),yy-len*Math.sin(ang+.5));}
    }
    children(a).forEach(draw);
  } children(n).forEach(draw);return y+vh*scale;
}
function panelHeight(n,w) {
  let h=27;
  for(const c of children(n)) {
    if(c.tag==='h3')h+=wrap(textContent(c),w-30,16).length*20+6;
    else if(c.tag==='ul') for(const li of children(c))h+=wrap(textContent(li),w-47,14).length*19+6;
    else h+=wrap(textContent(c),w-30,14).length*19+7;
  } return h;
}
function render(n,x,y,w) {
  if(n.tag==='svg') return drawSVG(n,x,y,w)+6;
  if(klass(n,'cols')){
    const count=klass(n,'two')?2:klass(n,'three')?3:4,gap=15,pw=(w-gap*(count-1))/count;
    const ps=children(n),h=Math.max(...ps.map(c=>panelHeight(c,pw)));
    for(let i=0;i<ps.length;i++) {const xx=x+i*(pw+gap);rect(xx,y,pw,h);line(xx,y,xx+pw,y,colors.teal,3);let yy=y+12;
      for(const c of children(ps[i])) {if(c.tag==='h3')yy=para(textContent(c),xx+15,yy,pw-30,16,true)+7;
        else if(c.tag==='ul'){for(const li of children(c)){text('-',xx+15,yy+14);yy=para(textContent(li),xx+29,yy,pw-44,14)+6;}}
        else yy=para(textContent(c),xx+15,yy,pw-30,14)+7;}
    }return y+h+12;
  }
  if(klass(n,'legend')) {let xx=x;for(const c of children(n)){const ww=width(textContent(c),12)+22;rect(xx,y,ww,24,colors[c.attrs.class]);text(textContent(c),xx+10,y+16,12);xx+=ww+14;}return y+30;}
  if(klass(n,'callout')){const h=wrap(textContent(n),w-32,14).length*19+24;rect(x,y,w,h,'#eaf4f5','#eaf4f5');line(x,y,x,y+h,colors.teal,4);para(textContent(n),x+16,y+10,w-32,14);return y+h+12;}
  if(klass(n,'pipeline')) {
    const title=children(n).find(c=>c.tag==='h3'),steps=children(n).find(c=>klass(c,'steps'));
    const cs=children(steps),gap=11,bw=(w-24-gap*5)/6;
    const h=Math.max(...cs.map(c=>wrap(textContent(c).replace(/^\d/,''),bw-20,14).length*19+48));
    rect(x,y,w,h+45,'#f1f5f8','#f1f5f8');text(textContent(title),x+12,y+23,16,true);
    cs.forEach((c,i)=>{const xx=x+12+i*(bw+gap);rect(xx,y+36,bw,h,'#ffffff');text(String(i+1),xx+10,y+56,14,true,colors.teal);para(textContent(c).replace(/^\d/,''),xx+10,y+62,bw-20,14);if(i<5)text('>',xx+bw+1,y+78,15,true,colors.teal);});
    return y+h+53;
  }
  if(klass(n,'journeys')) {for(const c of children(n)) y=render(c,x,y,w);return y;}
  if(klass(n,'journey')) {
    const title=children(n).find(c=>c.tag==='h3'),flow=children(n).find(c=>klass(c,'journeyline')),p=children(n).find(c=>c.tag==='p');
    const flows=children(flow).filter(c=>c.tag==='span'),fw=(w-65)/4;
    const fh=Math.max(...flows.map(c=>wrap(textContent(c),fw,14).length*19));
    const ph=wrap(textContent(p),w-32,13).length*18,h=45+fh+ph+15;
    rect(x,y,w,h,'#f4f8fa','#f4f8fa');line(x,y,x,y+h,colors.teal,4);text(textContent(title),x+16,y+24,16,true);
    flows.forEach((c,i)=>{para(textContent(c),x+16+i*(fw+10),y+34,fw,14);if(i<3)text('>',x+16+(i+1)*(fw+10)-10,y+48,14,true,colors.teal);});
    para(textContent(p),x+16,y+40+fh,w-32,13);return y+h+10;
  }
  if(klass(n,'roadmap')) {
    for(const row of children(n)) {const cs=children(row),rh=47;rect(x,y,w,rh,'#ffffff');line(x,y,x,y+rh,colors.teal,4);
      para(textContent(cs[0]),x+12,y+8,210,14,true);para(textContent(cs[1]),x+235,y+8,w-445,14);para(textContent(cs[2]),x+w-195,y+8,183,14,false,colors.muted);y+=rh+7;
    } return y+5;
  }
  if(n.tag==='p')return para(textContent(n),x,y,w,klass(n,'small')?12:14,false,colors.muted)+10;
  for(const c of children(n))y=render(c,x,y,w);return y;
}
function pdf(streams) {
  const objs=[];const add=s=>{objs.push(s);return objs.length;};
  add('<< /Type /Catalog /Pages 2 0 R >>');add('');
  add('<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>');
  add('<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>');
  const kids=[];
  for(const s of streams) {const stream=add(`<< /Length ${Buffer.byteLength(s)} >>\nstream\n${s}\nendstream`);
    kids.push(add(`<< /Type /Page /Parent 2 0 R /MediaBox [0 0 ${W} ${H}] /Resources << /Font << /F1 3 0 R /F2 4 0 R >> >> /Contents ${stream} 0 R >>`));}
  objs[1]=`<< /Type /Pages /Count ${kids.length} /Kids [${kids.map(id=>id+' 0 R').join(' ')}] >>`;
  let out='%PDF-1.4\n',offsets=[0];for(let i=0;i<objs.length;i++){offsets.push(Buffer.byteLength(out));out+=`${i+1} 0 obj\n${objs[i]}\nendobj\n`;}
  const xref=Buffer.byteLength(out);out+=`xref\n0 ${objs.length+1}\n0000000000 65535 f \n`+offsets.slice(1).map(x=>String(x).padStart(10,'0')+' 00000 n \n').join('');
  out+=`trailer\n<< /Size ${objs.length+1} /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF\n`;return out;
}
const root=parse(source),sections=[];
function walk(n){if(n.tag==='section'&&klass(n,'page'))sections.push(n);else children(n).forEach(walk);}walk(root);
const streams=[], checks=[];
for(const section of sections){commands=[];geometry=[];rect(0,0,W,H,'#ffffff','#ffffff',0);const header=find(section,'header'),main=find(section,'main'),footer=find(section,'footer');
  text(textContent(children(header)[0]),36,39,12,true,colors.teal);
  text(textContent(find(header,'h1')),36,75,29,true);
  para(textContent(children(header).find(x=>x.tag==='p')),36,87,1050,16,false,colors.muted);
  const end=render(main,36,139,1050);line(36,759,1086,759,colors.line,1);
  const foot=children(footer);text(textContent(foot[0]),36,776,10,false,colors.muted);text(textContent(foot[1]),1055,776,10,false,colors.muted);
  const bad=geometry.filter(t=>t.x<25||t.x+t.width>1095||t.y>784);
  if(end>750||bad.length)throw Error(`PDF overflow ${section.attrs.id}: y=${end} bad=${JSON.stringify(bad)}`);
  streams.push(commands.join('\n'));checks.push({page:section.attrs.id,body_bottom:end,text_runs:geometry.length,out_of_bounds:bad.length});
}
await fs.mkdir(path.join(dir,'previews'),{recursive:true});
await fs.writeFile(path.join(dir,'SelfLearning_Architecture_R1.pdf'),pdf(streams));
for(let i=0;i<streams.length;i++) {
  const single=path.join(dir,'previews',`page-${i+1}.pdf`);
  await fs.writeFile(single,pdf([streams[i]]));
  if(process.platform==='darwin')execFileSync('/usr/bin/sips',['-s','format','png',single,'--out',path.join(dir,'previews',`page-${i+1}.png`)],{stdio:'pipe'});
}
await fs.writeFile(path.join(dir,'render-verification.json'),JSON.stringify({renderer:'portable vector PDF from editable HTML',pages:sections.length,checks,pdf:'SelfLearning_Architecture_R1.pdf',visual_review:'required separately'},null,2)+'\n');
console.log(JSON.stringify({pages:sections.length,checks}));
