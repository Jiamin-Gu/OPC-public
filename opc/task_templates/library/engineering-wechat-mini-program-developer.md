## 技术交付物

### 小程序项目结构

```
miniprogram/
├── app.js                    # 应用入口
├── app.json                  # 全局配置
├── app.wxss                  # 全局样式
├── pages/
│   ├── index/                # 首页
│   │   ├── index.js
│   │   ├── index.json
│   │   ├── index.wxml
│   │   └── index.wxss
│   └── detail/               # 详情页
├── components/               # 公共组件
│   ├── nav-bar/              # 自定义导航栏
│   └── list-item/            # 列表项组件
├── utils/
│   ├── request.js            # 网络请求封装
│   ├── auth.js               # 登录鉴权
│   └── util.js               # 工具函数
├── services/                 # 业务接口层
├── constants/                # 常量定义
└── cloudfunctions/           # 云函数目录
    ├── login/
    └── pay/
```

### 网络请求封装

```javascript
// utils/request.js
const BASE_URL = 'https://api.example.com'

const request = (options) => {
  return new Promise((resolve, reject) => {
    const token = wx.getStorageSync('token')

    wx.request({
      url: `${BASE_URL}${options.url}`,
      method: options.method || 'GET',
      data: options.data,
      header: {
        'Content-Type': 'application/json',
        'Authorization': token ? `Bearer ${token}` : '',
        ...options.header,
      },
      success: (res) => {
        if (res.statusCode === 200) {
          resolve(res.data)
        } else if (res.statusCode === 401) {
          // token 过期，重新登录
          refreshToken().then(() => {
            request(options).then(resolve).catch(reject)
          })
        } else {
          reject(new Error(res.data.message || '请求失败'))
        }
      },
      fail: (err) => {
        reject(new Error('网络异常，请检查网络连接'))
      },
    })
  })
}

// 带 loading 的请求封装
const requestWithLoading = async (options) => {
  wx.showLoading({ title: '加载中...', mask: true })
  try {
    const result = await request(options)
    return result
  } catch (err) {
    wx.showToast({ title: err.message, icon: 'none' })
    throw err
  } finally {
    wx.hideLoading()
  }
}

module.exports = { request, requestWithLoading }
```

### 微信支付集成示例

```javascript
// 云函数：pay/index.js
const cloud = require('wx-server-sdk')
cloud.init({ env: cloud.DYNAMIC_CURRENT_ENV })

exports.main = async (event, context) => {
  const { orderId, totalFee, description } = event
  const wxContext = cloud.getWXContext()

  const res = await cloud.cloudPay.unifiedOrder({
    body: description,
    outTradeNo: orderId,
    totalFee: totalFee, // 单位：分
    spbillCreateIp: '127.0.0.1',
    envId: cloud.DYNAMIC_CURRENT_ENV,
    functionName: 'payCallback', // 支付回调云函数
    nonceStr: generateNonceStr(),
    tradeType: 'JSAPI',
  })

  return res
}

// 前端调起支付
const handlePay = async (orderId, totalFee, description) => {
  try {
    const payParams = await wx.cloud.callFunction({
      name: 'pay',
      data: { orderId, totalFee, description },
    })

    const { payment } = payParams.result
    await wx.requestPayment({
      ...payment,
    })

    wx.showToast({ title: '支付成功' })
  } catch (err) {
    if (err.errMsg !== 'requestPayment:fail cancel') {
      wx.showToast({ title: '支付失败', icon: 'none' })
    }
  }
}
```

### 分包配置示例

```json
{
  "pages": [
    "pages/index/index",
    "pages/mine/mine"
  ],
  "subpackages": [
    {
      "root": "packageA",
      "pages": [
        "pages/detail/detail",
        "pages/list/list"
      ]
    },
    {
      "root": "packageB",
      "independent": true,
      "pages": [
        "pages/share/share"
      ]
    }
  ],
  "preloadRule": {
    "pages/index/index": {
      "network": "all",
      "packages": ["packageA"]
    }
  }
}
```
