import data from './data.json';
export function previewApi(path){
 const [route,query='']=path.split('?'),params=new URLSearchParams(query);
 if(route==='/services')return data.services;
 if(route==='/stats')return data.summaries[params.get('status')||''];
 if(route==='/traces')return {...data.traces,traces:data.traces.traces.filter(t=>(!params.get('status')||t.status===params.get('status'))&&(!params.get('service')||t.service===params.get('service')))};
 if(route.startsWith('/traces/'))return data.details[route.slice(8)];
 throw Error('Not available in the static preview');
}
