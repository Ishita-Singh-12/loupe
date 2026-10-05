import {chromium} from 'playwright';
import {spawn} from 'node:child_process';
import {mkdir} from 'node:fs/promises';
import assert from 'node:assert/strict';
const port=4273,base='http://127.0.0.1:'+port;
const server=spawn('node',['server/index.js'],{env:{...process.env,PORT:String(port),MONGODB_URI:'',LOUPE_API_KEY:'',NODE_ENV:'test'},stdio:['ignore','pipe','pipe']});
let logs='';server.stdout.on('data',d=>logs+=d);server.stderr.on('data',d=>logs+=d);
let browser;
try{
 for(let i=0;i<200;i++){try{if((await fetch(base+'/api/health')).ok)break;}catch{}if(server.exitCode!==null)throw Error(logs);await new Promise(r=>setTimeout(r,100));}
 await new Promise((resolve,reject)=>{const demo=spawn('python3',['demo/agent.py'],{env:{...process.env,LOUPE_ENDPOINT:base,LOUPE_API_KEY:''},stdio:['ignore','pipe','pipe']});demo.stdout.on('data',d=>process.stdout.write(d));demo.stderr.on('data',d=>process.stderr.write(d));demo.on('exit',code=>code===0?resolve():reject(Error('Demo failed')));});
 const stats=await(await fetch(base+'/api/stats')).json();assert.equal(stats.summary.total,8);assert.equal(stats.summary.errors,2);assert.equal(stats.summary.success,6);
 browser=await chromium.launch({executablePath:process.env.CHROME_PATH||'/usr/bin/google-chrome',headless:true,args:['--no-sandbox']});
 const page=await browser.newPage({viewport:{width:1440,height:1000}});const errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto(base);await page.getByRole('button',{name:'documentation agent',exact:true}).first().waitFor();
 await mkdir('artifacts',{recursive:true});await page.screenshot({path:'artifacts/dashboard-desktop.png',fullPage:true});
 await page.getByRole('button',{name:'documentation agent',exact:true}).first().click();await page.getByRole('dialog').waitFor();await page.getByText('Span waterfall',{exact:true}).waitFor();
 await page.screenshot({path:'artifacts/waterfall-desktop.png',fullPage:true});await page.getByRole('button',{name:'Close trace'}).click();
 await page.getByLabel('Status filter').selectOption('ERROR');await page.waitForFunction(()=>document.querySelectorAll('tbody tr').length===2);
 await page.getByRole('button',{name:'documentation agent',exact:true}).first().click();await page.getByRole('dialog').waitFor();await page.waitForFunction(()=>document.querySelector('.span-info')?.textContent.includes('TimeoutError'));
 await page.screenshot({path:'artifacts/failure-desktop.png',fullPage:true});await page.getByRole('button',{name:'Close trace'}).click();
 await page.setViewportSize({width:390,height:844});await page.screenshot({path:'artifacts/dashboard-mobile.png',fullPage:true});
 assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>window.innerWidth),false);await page.getByRole('button',{name:'documentation agent',exact:true}).first().click();await page.getByRole('dialog').waitFor();await page.screenshot({path:'artifacts/waterfall-mobile.png',fullPage:true});
 assert.deepEqual(errors,[]);console.log('UI passed: real demo traces, waterfall, error filter, failure details, mobile no-overflow');
}finally{await browser?.close();server.kill('SIGTERM');}
