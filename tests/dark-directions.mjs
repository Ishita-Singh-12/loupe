import {chromium} from 'playwright';import {spawn} from 'node:child_process';import {mkdir} from 'node:fs/promises';
const port=4373,base='http://127.0.0.1:'+port;
const server=spawn('node',['server/index.js'],{env:{...process.env,PORT:String(port),MONGODB_URI:'',LOUPE_API_KEY:'',NODE_ENV:'test'},stdio:'ignore'});let browser;
try{for(let i=0;i<200;i++){try{if((await fetch(base+'/api/health')).ok)break;}catch{}await new Promise(r=>setTimeout(r,100));}
await new Promise((resolve,reject)=>{const p=spawn('python3',['demo/agent.py'],{env:{...process.env,LOUPE_ENDPOINT:base},stdio:'inherit'});p.on('exit',c=>c===0?resolve():reject(Error('Demo failed')));});
browser=await chromium.launch({executablePath:'/usr/bin/google-chrome',args:['--no-sandbox']});const page=await browser.newPage({viewport:{width:1440,height:1020}});await mkdir('artifacts',{recursive:true});for(let i=1;i<=4;i++){await page.goto(base+'/?dark='+i);await page.getByText('documentation agent',{exact:true}).first().waitFor();await page.screenshot({path:'artifacts/dark-'+i+'.png',fullPage:true});}console.log('Three previews rendered from live local traces');}finally{await browser?.close();server.kill('SIGTERM');}
