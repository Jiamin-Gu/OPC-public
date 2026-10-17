## 技术交付物

### 钉钉应用项目结构

```
dingtalk-integration/
├── src/
│   ├── config/
│   │   ├── dingtalk.ts             # 钉钉应用配置
│   │   └── env.ts                  # 环境变量管理
│   ├── auth/
│   │   ├── token-manager.ts        # access_token 获取与缓存
│   │   └── callback-verify.ts      # 回调签名验证
│   ├── bot/
│   │   ├── stream-client.ts        # Stream 模式机器人
│   │   ├── command-handler.ts      # 指令解析与路由
│   │   ├── message-sender.ts       # 消息发送封装
│   │   └── card-builder.ts         # 互动卡片构建
│   ├── approval/
│   │   ├── process-define.ts       # 审批流程定义
│   │   ├── instance-manager.ts     # 审批实例管理
│   │   └── event-handler.ts        # 审批事件回调
│   ├── connector/
│   │   ├── custom-connector.ts     # 自定义连接器
│   │   └── flow-trigger.ts         # 流程触发器
│   ├── miniapp/
│   │   ├── auth-handler.ts         # 小程序免登
│   │   └── jsapi-bridge.ts         # JSAPI 桥接
│   ├── contacts/
│   │   ├── department-sync.ts      # 部门同步
│   │   └── user-sync.ts            # 用户信息同步
│   ├── webhook/
│   │   ├── event-dispatcher.ts     # 事件分发器
│   │   └── handlers/               # 各类事件处理器
│   └── utils/
│       ├── http-client.ts          # HTTP 请求封装
│       ├── logger.ts               # 日志工具
│       └── retry.ts                # 重试与限流处理
├── tests/
├── docker-compose.yml
└── package.json
```

### Token 管理与请求封装

```typescript
// src/auth/token-manager.ts

class DingTalkTokenManager {
  private token: string = '';
  private expireAt: number = 0;

  constructor(
    private appKey: string,
    private appSecret: string
  ) {}

  async getAccessToken(): Promise<string> {
    // 提前 10 分钟刷新
    if (this.token && Date.now() < this.expireAt - 600 * 1000) {
      return this.token;
    }

    const resp = await fetch(
      'https://oapi.dingtalk.com/gettoken?' +
      `appkey=${this.appKey}&appsecret=${this.appSecret}`
    );

    const data = await resp.json();
    if (data.errcode !== 0) {
      throw new Error(`获取 access_token 失败: ${data.errmsg}`);
    }

    this.token = data.access_token;
    this.expireAt = Date.now() + data.expires_in * 1000;
    return this.token;
  }
}

// 新版 API（推荐）：使用钉钉 SDK
import DingTalk from 'dingtalk-sdk';

const client = new DingTalk({
  appKey: process.env.DINGTALK_APP_KEY!,
  appSecret: process.env.DINGTALK_APP_SECRET!,
});

export { client };
export const tokenManager = new DingTalkTokenManager(
  process.env.DINGTALK_APP_KEY!,
  process.env.DINGTALK_APP_SECRET!
);
```

### Stream 模式机器人

```typescript
// src/bot/stream-client.ts
import { DWClient, DWClientDownStream, TOPIC_ROBOT } from 'dingtalk-stream';

const client = new DWClient({
  clientId: process.env.DINGTALK_APP_KEY!,
  clientSecret: process.env.DINGTALK_APP_SECRET!,
});

// 注册机器人消息回调
client.registerCallbackListener(TOPIC_ROBOT, async (res: DWClientDownStream) => {
  const data = JSON.parse(res.data);
  const text = data?.text?.content?.trim() || '';
  const senderId = data?.senderStaffId;
  const conversationType = data?.conversationType; // 1=单聊 2=群聊
  const conversationId = data?.conversationId;

  let replyContent = '';

  // 指令路由
  if (text.startsWith('/help')) {
    replyContent = '可用指令：\n/help - 帮助\n/status - 系统状态\n/approve - 发起审批';
  } else if (text.startsWith('/status')) {
    replyContent = await getSystemStatus();
  } else if (text.startsWith('/approve')) {
    replyContent = await createApproval(senderId, text);
  } else {
    replyContent = `收到消息：${text}\n输入 /help 查看可用指令`;
  }

  // 回复消息
  client.sendCardCallBack(res.headers, JSON.stringify({
    msgtype: 'text',
    text: { content: replyContent }
  }));
});

client.connect();
```

### 工作通知发送

```typescript
// src/bot/message-sender.ts

// 发送工作通知（消息到个人，阅读率最高）
async function sendWorkNotification(params: {
  userIds: string[];
  content: string;
  msgType?: 'text' | 'markdown' | 'action_card';
}) {
  const token = await tokenManager.getAccessToken();

  const body: any = {
    agent_id: process.env.DINGTALK_AGENT_ID,
    userid_list: params.userIds.join(','),
    msg: {},
  };

  if (params.msgType === 'markdown') {
    body.msg = {
      msgtype: 'markdown',
      markdown: {
        title: '通知',
        text: params.content,
      },
    };
  } else {
    body.msg = {
      msgtype: 'text',
      text: { content: params.content },
    };
  }

  const resp = await fetch(
    `https://oapi.dingtalk.com/topapi/message/corpconversation/asyncsend_v2?access_token=${token}`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }
  );

  const data = await resp.json();
  if (data.errcode !== 0) {
    throw new Error(`发送工作通知失败: ${data.errmsg}`);
  }
  return data.task_id;
}

// 发送群机器人消息（Webhook 方式）
async function sendGroupRobotMessage(params: {
  webhookUrl: string;
  secret: string;
  content: string;
  atUserIds?: string[];
}) {
  const timestamp = Date.now();
  const sign = computeHmacSha256(`${timestamp}\n${params.secret}`, params.secret);

  const url = `${params.webhookUrl}&timestamp=${timestamp}&sign=${encodeURIComponent(sign)}`;

  const body: any = {
    msgtype: 'markdown',
    markdown: {
      title: '通知',
      text: params.content,
    },
    at: {
      atUserIds: params.atUserIds || [],
      isAtAll: false,
    },
  };

  const resp = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });

  const data = await resp.json();
  if (data.errcode !== 0) {
    throw new Error(`发送群消息失败: ${data.errmsg}`);
  }
}
```

### 审批流集成

```typescript
// src/approval/instance-manager.ts

// 发起审批实例
async function createApprovalInstance(params: {
  processCode: string;
  originatorUserId: string;
  deptId: number;
  formValues: Array<{ name: string; value: string }>;
  approvers?: Array<{ actionType: string; userIds: string[] }>;
}) {
  const token = await tokenManager.getAccessToken();

  const resp = await fetch(
    `https://oapi.dingtalk.com/topapi/processinstance/create?access_token=${token}`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        process_code: params.processCode,
        originator_user_id: params.originatorUserId,
        dept_id: params.deptId,
        form_component_values: params.formValues,
        approvers_v2: params.approvers,
      }),
    }
  );

  const data = await resp.json();
  if (data.errcode !== 0) {
    throw new Error(`发起审批失败: ${data.errmsg}`);
  }
  return data.process_instance_id;
}

// 查询审批实例详情
async function getApprovalInstance(processInstanceId: string) {
  const token = await tokenManager.getAccessToken();

  const resp = await fetch(
    `https://oapi.dingtalk.com/topapi/processinstance/get?access_token=${token}`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ process_instance_id: processInstanceId }),
    }
  );

  const data = await resp.json();
  if (data.errcode !== 0) {
    throw new Error(`查询审批实例失败: ${data.errmsg}`);
  }
  return data.process_instance;
}

// 审批事件回调处理
async function handleApprovalEvent(event: {
  EventType: string;
  processInstanceId: string;
  result: string;
  type: string;
}) {
  const instanceId = event.processInstanceId;

  switch (event.type) {
    case 'finish':
      if (event.result === 'agree') {
        await onApprovalApproved(instanceId);
      } else {
        await onApprovalRejected(instanceId);
      }
      break;
    case 'start':
      await onApprovalStarted(instanceId);
      break;
    case 'terminate':
      await onApprovalTerminated(instanceId);
      break;
  }
}
```

### 回调签名验证

```typescript
// src/auth/callback-verify.ts
import crypto from 'crypto';

// HTTP 回调模式的签名验证
function verifyCallbackSignature(
  token: string,
  timestamp: string,
  nonce: string,
  encrypt: string,
  signature: string
): boolean {
  const sortedStr = [token, timestamp, nonce, encrypt].sort().join('');
  const computedSignature = crypto
    .createHash('sha1')
    .update(sortedStr)
    .digest('hex');
  return computedSignature === signature;
}

// 解密回调数据
function decryptCallbackData(
  encrypt: string,
  encodingAesKey: string
): string {
  const aesKey = Buffer.from(encodingAesKey + '=', 'base64');
  const iv = aesKey.slice(0, 16);
  const decipher = crypto.createDecipheriv('aes-256-cbc', aesKey, iv);
  decipher.setAutoPadding(false);

  let decrypted = Buffer.concat([
    decipher.update(Buffer.from(encrypt, 'base64')),
    decipher.final(),
  ]);

  // PKCS7 去填充
  const pad = decrypted[decrypted.length - 1];
  decrypted = decrypted.slice(0, decrypted.length - pad);

  // 去掉前 20 字节的随机数据和 4 字节的消息长度
  const msgLen = decrypted.readInt32BE(16);
  return decrypted.slice(20, 20 + msgLen).toString('utf-8');
}

export { verifyCallbackSignature, decryptCallbackData };
```
