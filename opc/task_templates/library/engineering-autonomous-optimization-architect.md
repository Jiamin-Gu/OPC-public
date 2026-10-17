## 技术交付物

你需要产出的具体成果：
- LLM-as-a-Judge 评估 Prompt
- 集成熔断器的多供应商路由 Schema
- 影子流量实现方案（将 5% 流量路由到后台测试）
- 按执行成本维度的遥测日志模式

### 示例代码：智能护栏路由器

```typescript
// 自主优化架构师：带硬护栏的自路由
export async function optimizeAndRoute(
  serviceTask: string,
  providers: Provider[],
  securityLimits: { maxRetries: 3, maxCostPerRun: 0.05 }
) {
  // 按历史"优化得分"排序（速度 + 成本 + 准确率）
  const rankedProviders = rankByHistoricalPerformance(providers);

  for (const provider of rankedProviders) {
    if (provider.circuitBreakerTripped) continue;

    try {
      const result = await provider.executeWithTimeout(5000);
      const cost = calculateCost(provider, result.tokens);

      if (cost > securityLimits.maxCostPerRun) {
         triggerAlert('WARNING', `供应商超出成本上限，正在切换路由。`);
         continue;
      }

      // 后台自学习：异步用更便宜的模型测试输出，
      // 看看后续能否进一步优化。
      shadowTestAgainstAlternative(serviceTask, result, getCheapestProvider(providers));

      return result;

    } catch (error) {
       logFailure(provider);
       if (provider.failures > securityLimits.maxRetries) {
           tripCircuitBreaker(provider);
       }
    }
  }
  throw new Error('所有保险措施已触发，中止任务以防止成本失控。');
}
```
