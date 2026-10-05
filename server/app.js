import express from 'express';
import helmet from 'helmet';
import rateLimit from 'express-rate-limit';
import mongoose from 'mongoose';
import {z} from 'zod';
import path from 'node:path';
import {timingSafeEqual} from 'node:crypto';
import {estimateCost,DEFAULT_PRICING} from './pricing.js';
const attributes=z.record(z.union([z.string().max(200),z.number().finite(),z.boolean(),z.null()]));
const date=z.string().datetime({offset:true});
const input=z.object({trace_id:z.string().regex(/^[a-f0-9]{32}$/).refine(s=>!/^0+$/.test(s)),span_id:z.string().regex(/^[a-f0-9]{16}$/).refine(s=>!/^0+$/.test(s)),parent_span_id:z.string().regex(/^[a-f0-9]{16}$/).nullable(),name:z.string().min(1).max(120),kind:z.enum(['INTERNAL','CLIENT','SERVER','PRODUCER','CONSUMER']),started_at:date,ended_at:date,status:z.enum(['UNSET','OK','ERROR']),attributes:attributes.default({}),resource:attributes.default({})}).strict().superRefine((s,c)=>{
 if(Date.parse(s.ended_at)<Date.parse(s.started_at)) c.addIssue({code:'custom',message:'End must follow start'});
 if(s.span_id===s.parent_span_id)c.addIssue({code:'custom',message:'Span cannot parent itself'});
 for(const key of ['gen_ai.usage.input_tokens','gen_ai.usage.output_tokens','gen_ai.usage.cache_read.input_tokens','gen_ai.usage.cache_write.input_tokens'])if(s.attributes[key]!==undefined&&(!Number.isSafeInteger(s.attributes[key])||s.attributes[key]<0))c.addIssue({code:'custom',message:'Invalid token count'});
 if(Object.keys(s.attributes).length>64 || Object.keys(s.resource).length>16)c.addIssue({code:'custom',message:'Too many attributes'});
});
const schema=new mongoose.Schema({trace_id:String,span_id:String,parent_span_id:String,name:String,kind:String,started_at:Date,ended_at:Date,status:String,attributes:mongoose.Schema.Types.Mixed,resource:mongoose.Schema.Types.Mixed,duration_ms:Number,cost_usd:Number,pricing_version:String,pricing_reason:String},{versionKey:false});
schema.index({trace_id:1,span_id:1},{unique:true});schema.index({started_at:-1});schema.index({status:1,started_at:-1});schema.index({trace_id:1,started_at:1});
export const Span=mongoose.models.Span||mongoose.model('Span',schema);
// Span-level success isn't task success. Roll up all spans into distinct traces first.
export const tracePipeline=[{$group:{_id:'$trace_id',started_at:{$min:'$started_at'},ended_at:{$max:'$ended_at'},name:{$first:'$name'},service:{$first:{$getField:{field:'service.name',input:'$resource'}}},span_count:{$sum:1},errors:{$sum:{$cond:[{$eq:['$status','ERROR']},1,0]}},input_tokens:{$sum:{$ifNull:[{$getField:{field:'gen_ai.usage.input_tokens',input:'$attributes'}},0]}},output_tokens:{$sum:{$ifNull:[{$getField:{field:'gen_ai.usage.output_tokens',input:'$attributes'}},0]}},known_cost:{$sum:{$ifNull:['$cost_usd',0]}},unpriced:{$sum:{$cond:[{$and:[{$ne:[{$ifNull:[{$getField:{field:'gen_ai.request.model',input:'$attributes'}},null]},null]},{$eq:['$cost_usd',null]}]},1,0]}},root:{$max:{$cond:[{$eq:['$parent_span_id',null]},1,0]}}}},{$addFields:{duration_ms:{$subtract:['$ended_at','$started_at']},status:{$cond:[{$gt:['$errors',0]},'ERROR',{$cond:[{$eq:['$root',1]},'OK','INCOMPLETE']}]}}}];
const safe=fn=>(req,res,next)=>Promise.resolve(fn(req,res)).catch(next);
export function createApp({apiKey=process.env.LOUPE_API_KEY,pricing=DEFAULT_PRICING}={}){
 const app=express();app.use(helmet());app.use(express.json({limit:'1mb'}));app.use('/api',rateLimit({windowMs:60000,limit:240}));
 if(apiKey)app.use('/api',(req,res,next)=>{const a=Buffer.from(req.headers.authorization||''),b=Buffer.from('Bearer '+apiKey);if(a.length!==b.length||!timingSafeEqual(a,b))return res.status(401).json({error:'Unauthorized'});next();});
 app.get('/api/health',(_,res)=>res.json({ok:true,database:mongoose.connection.readyState===1?'connected':'disconnected'}));
 app.post('/api/spans',safe(async(req,res)=>{
  const {spans}=z.object({spans:z.array(input).min(1).max(100)}).strict().parse(req.body);
  const rows=spans.map(s=>({...s,started_at:new Date(s.started_at),ended_at:new Date(s.ended_at),duration_ms:Date.parse(s.ended_at)-Date.parse(s.started_at),...estimateCost(s.attributes,pricing)}));
  await Span.bulkWrite(rows.map(s=>({updateOne:{filter:{trace_id:s.trace_id,span_id:s.span_id},update:{$setOnInsert:s},upsert:true}})),{ordered:false});
  res.json({accepted:rows.length});
 }));
 const filter=req=>{const query=z.object({status:z.enum(['OK','ERROR','INCOMPLETE']).optional(),service:z.string().max(100).optional(),days:z.coerce.number().int().min(1).max(90).default(7)}).parse(req.query);return query;};
 const pipeline=req=>{const q=filter(req);const result=[{$match:{started_at:{$gte:new Date(Date.now()-q.days*86400000)}}},{$sort:{parent_span_id:1,started_at:1}},...tracePipeline];const match={};if(q.status)match.status=q.status;if(q.service)match.service=q.service;if(Object.keys(match).length)result.push({$match:match});return result;};
 app.get('/api/traces',safe(async(req,res)=>res.json({traces:await Span.aggregate([...pipeline(req),{$sort:{started_at:-1}},{$limit:100}])})));
 app.get('/api/traces/:id',safe(async(req,res)=>{if(!/^[a-f0-9]{32}$/.test(req.params.id))return res.status(400).json({error:'Invalid trace ID'});const spans=await Span.find({trace_id:req.params.id}).sort({started_at:1}).lean();if(!spans.length)return res.status(404).json({error:'Trace not found'});res.json({trace_id:req.params.id,spans});}));
 app.get('/api/stats',safe(async(req,res)=>{
  const [result]=await Span.aggregate([...pipeline(req),{$facet:{summary:[{$group:{_id:null,total:{$sum:1},success:{$sum:{$cond:[{$eq:['$status','OK']},1,0]}},errors:{$sum:{$cond:[{$eq:['$status','ERROR']},1,0]}},incomplete:{$sum:{$cond:[{$eq:['$status','INCOMPLETE']},1,0]}},known_cost:{$sum:'$known_cost'},unpriced:{$sum:'$unpriced'},input_tokens:{$sum:'$input_tokens'},output_tokens:{$sum:'$output_tokens'},p95:{$percentile:{input:'$duration_ms',p:[.95],method:'approximate'}}}}],daily:[{$group:{_id:{$dateToString:{format:'%Y-%m-%d',date:'$started_at'}},runs:{$sum:1},known_cost:{$sum:'$known_cost'},unpriced:{$sum:'$unpriced'}}},{$sort:{_id:1}}]}}]);
  res.json({summary:result.summary[0]||{total:0,success:0,errors:0,incomplete:0,known_cost:0,unpriced:0,input_tokens:0,output_tokens:0,p95:[0]},daily:result.daily});
 }));
 app.get('/api/services',safe(async(_,res)=>res.json({services:(await Span.aggregate([{$group:{_id:{$getField:{field:'service.name',input:'$resource'}}}},{$match:{_id:{$ne:null}}}])).map(s=>s._id)})));
 app.use(express.static(path.resolve('dist')));app.get('*',(req,res)=>req.path.startsWith('/api')?res.status(404).json({error:'Not found'}):res.sendFile(path.resolve('dist/index.html')));
 app.use((err,req,res,next)=>{if(err instanceof z.ZodError||err.status===400)return res.status(400).json({error:'Invalid span or query',details:err.issues?.map(i=>i.message)});console.error(err.name);res.status(500).json({error:'Internal server error'});});return app;
}
