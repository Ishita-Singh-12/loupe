// Estimates, never billing. Unknown/cached-token spans stay unpriced unless a full rate is supplied.
// Demo fixture is deliberately separate from provider pricing. Add reviewed rates in PRICING_FILE.
export const DEFAULT_PRICING = {version:'demo-v1', updated_at:'2026-10-05', models:{
 'loupe-local-demo':{input_per_million:0,output_per_million:0,source:'Local deterministic model, no provider charges'}
}};
export function estimateCost(attributes, table=DEFAULT_PRICING) {
 const model=attributes['gen_ai.response.model']||attributes['gen_ai.request.model'];
 const input=attributes['gen_ai.usage.input_tokens'], output=attributes['gen_ai.usage.output_tokens'];
 const rate=table.models[model];
 const cached=attributes['gen_ai.usage.cache_read.input_tokens']||attributes['gen_ai.usage.cache_write.input_tokens'];
 if(!rate || input===undefined || output===undefined || cached) return {cost_usd:null,pricing_version:table.version,pricing_reason:cached?'cached tokens require a full pricing policy':'unknown model or missing usage'};
 return {cost_usd:(input*rate.input_per_million+output*rate.output_per_million)/1e6,pricing_version:table.version,pricing_reason:'token estimate'};
}
