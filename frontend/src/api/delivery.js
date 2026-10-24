import request from './request'
// Shared presentation components receive an explicit transport, never import desktop auth.
export async function deliveryTransport(method, path, data, options={}) {
  const match=path.match(/^\/api\/workspaces\/([^/]+)(.*)$/)
  const params={...(options.params||{})}
  if(match){params.workspace_id=decodeURIComponent(match[1]);path=match[2]==='/delivery-members'?'/api/deliveries/members':'/api'+match[2]}
  if(path.endsWith('/intake'))data={...data,workspace_id:params.workspace_id}
  const result=await request({method,url:path,data,params,responseType:options.responseType,timeout:600000,_silent:true})
  return options.responseType==='blob'?result.data:result
}
