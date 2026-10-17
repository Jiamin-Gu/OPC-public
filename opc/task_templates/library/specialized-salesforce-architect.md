## 集成模式模板

```
┌──────────────┐     ┌───────────────┐     ┌──────────────┐
│  源系统       │────▶│  中间件        │────▶│  Salesforce   │
│  Source       │     │  (MuleSoft)   │     │  (Platform    │
│              │◀────│               │◀────│   Events)     │
└──────────────┘     └───────────────┘     └──────────────┘
         │                    │                      │
    [Auth: OAuth2]    [Transform: DataWeave]  [Trigger → Handler]
    [Format: JSON]    [Retry: 3x exp backoff] [Bulk: 200/batch]
    [Rate: 100/min]   [DLQ: error__c object]  [Async: Queueable]
```
