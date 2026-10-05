import {spawn} from 'node:child_process';
import {mkdir,writeFile} from 'node:fs/promises';
const base='http://127.0.0.1:4473';
const server=spawn('node',['server/index.js'],{env:{...process.env,PORT:'4473',MONGODB_URI:'',LOUPE_API_KEY:'',NODE_ENV:'test'},stdio:'ignore'});
try{for(let i=0;i<200;i++){try{if((await fetch(base+'/api/health')).ok)break;}catch{}await new Promise(r=>setTimeout(r,100));}
await new Promise((resolve,reject)=>{const p=spawn('python3',['demo/agent.py'],{env:{...process.env,LOUPE_ENDPOINT:base},stdio:'inherit'});p.on('exit',c=>c===0?resolve():reject(Error('Demo failed')));});
const get=path=>fetch(base+'/api'+path).then(r=>r.json());
const traces=await get('/traces'),services=await get('/services'),details={};
for(const t of traces.traces)details[t._id]=await get('/traces/'+t._id);
const summaries={};for(const status of ['', 'OK','ERROR','INCOMPLETE'])summaries[status]=await get('/stats'+(status?'?status='+status:''));
await mkdir('client/src/preview',{recursive:true});await writeFile('client/src/preview/data.json',JSON.stringify({traces,services,details,summaries},null,2));
}finally{server.kill('SIGTERM');}
