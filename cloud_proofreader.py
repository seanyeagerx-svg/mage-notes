"""Bounded GLM text service. No agents/tools, redirects, audio uploads or paid fallback."""
import json
import threading
import time
import urllib.error
import urllib.request
from ai_proofreader import INSTRUCTIONS
from provider_secret import KeyStore

ENDPOINT='https://open.bigmodel.cn/api/paas/v4/chat/completions'
MODEL='glm-5.3-flash'
_gates={}
_gates_lock=threading.Lock()


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None


class ProviderError(RuntimeError):pass


class GLMProofreader:
    evidence_provider=True
    def __init__(self,root,timeout=10,transport=None):
        self.keys=KeyStore(root);self.timeout=timeout;self.closed=threading.Event()
        with _gates_lock:
            self.shared=_gates.setdefault(str(self.keys.path.resolve()).casefold(),{'lock':threading.Lock(),'retry_at':0})
            self.gate=self.shared['lock']
        self.last_backend='glm';self.last_error=None;self.last_usage=None;self.retry_at=0
        self.transport=transport or urllib.request.build_opener(NoRedirect()).open
    def start(self):
        if self.closed.is_set():raise ProviderError('AI 校对已关闭')
        if not self.keys.exists():raise ProviderError('尚未填写 GLM API Key；保留本地结果')
    def request_json(self,instructions,payload,max_tokens=600):
        self.start()
        if time.monotonic()<max(self.retry_at,self.shared['retry_at']):raise ProviderError('GLM 暂时不可用，保留本地结果；请稍后重试')
        if not self.gate.acquire(blocking=False):raise ProviderError('GLM 正在处理另一项请求；保留本地结果，稍后可重试')
        try:
            key=self.keys.load()
            body=dict(model=MODEL,messages=[{'role':'system','content':instructions},
                {'role':'user','content':json.dumps(payload,ensure_ascii=False)}],
                # 5.3 Flash requires thinking; its token limit includes reasoning.
                thinking={'type':'enabled'},max_tokens=min(4096,max(1,max_tokens)+1536),
                response_format={'type':'json_object'},stream=False)
            request=urllib.request.Request(ENDPOINT,json.dumps(body,ensure_ascii=False).encode('utf-8'),
                {'Content-Type':'application/json','Authorization':'Bearer '+key},method='POST')
            with self.transport(request,timeout=self.timeout) as response:
                raw=response.read(131073)
            if self.closed.is_set():raise ProviderError('AI 校对已关闭')
            if len(raw)>131072:raise ProviderError('AI 返回内容过长，保留原文')
            result=json.loads(raw);choice=result['choices'][0];message=choice['message']
            if choice.get('finish_reason')!='stop' or message.get('tool_calls') or message.get('function_call'):
                raise ProviderError('AI 返回了不完整内容或操作，已拒绝')
            value=json.loads(message['content'])
            if not isinstance(value,dict):raise ValueError('object required')
            self.last_usage=result.get('usage');self.last_error=None
            return value
        except urllib.error.HTTPError as exc:
            provider_code=''
            try:
                failure=json.loads(exc.read(8192))
                provider_code=str(failure.get('error',{}).get('code',''))
            except Exception:pass
            exc.close()
            self.retry_at=time.monotonic()+60
            self.shared['retry_at']=self.retry_at
            label={401:'密钥无效',403:'账号没有访问权限',429:'请求过于频繁或额度受限'}.get(exc.code,'服务暂不可用')
            if exc.code==429 and provider_code=='1305':label='模型当前访问量过大，请稍后重试'
            self.last_error=f'HTTP{exc.code}'+('/'+provider_code if provider_code.isdigit() and len(provider_code)<8 else '')
            raise ProviderError('GLM '+label+'；保留本地结果') from None
        except ProviderError:
            raise
        except TimeoutError:
            self.last_error='Timeout';self.retry_at=time.monotonic()+60
            self.shared['retry_at']=self.retry_at
            raise ProviderError('GLM 响应超时；保留本地结果，可结束听写后手动校对') from None
        except Exception:
            self.last_error='ConnectionOrResponseError';self.retry_at=time.monotonic()+30
            self.shared['retry_at']=self.retry_at
            raise ProviderError('GLM 连接或返回异常，保留本地结果') from None
        finally:
            self.gate.release()
    def propose_with_evidence(self,text,acoustic='',context='',protected=(),support=''):
        payload=dict(original=text[:300],acoustic=acoustic[:320],previous=context[-160:],support=(support or '')[:320])
        relevant=''.join(payload.values())
        payload['protected']=[p for p in protected if isinstance(p,str) and 0<len(p)<=48 and p in relevant][:24]
        instructions=INSTRUCTIONS+'\n输出结构必须且只能为 {"text":"校对后的本句"}，不使用 original、corrected_text、explanation 等其他字段。不修改时也用 text 返回原句。'
        result=self.request_json(instructions,payload)
        if set(result)!={'text'} or not isinstance(result['text'],str) or not 0<len(result['text'].strip())<=600:
            raise ProviderError('GLM 校对格式不正确，保留原文')
        return result['text'].strip()
    def propose(self,text):return self.propose_with_evidence(text)
    def close(self):self.closed.set()
