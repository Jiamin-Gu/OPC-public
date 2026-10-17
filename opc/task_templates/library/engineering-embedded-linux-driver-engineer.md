## 技术交付物

### Platform Driver 模板

```c
#include <linux/module.h>
#include <linux/platform_device.h>
#include <linux/of.h>
#include <linux/io.h>

struct mydev_priv {
    void __iomem *base;
    struct clk *clk;
    int irq;
};

static int mydev_probe(struct platform_device *pdev)
{
    struct mydev_priv *priv;
    struct resource *res;

    priv = devm_kzalloc(&pdev->dev, sizeof(*priv), GFP_KERNEL);
    if (!priv)
        return -ENOMEM;

    res = platform_get_resource(pdev, IORESOURCE_MEM, 0);
    priv->base = devm_ioremap_resource(&pdev->dev, res);
    if (IS_ERR(priv->base))
        return PTR_ERR(priv->base);

    priv->clk = devm_clk_get(&pdev->dev, NULL);
    if (IS_ERR(priv->clk))
        return PTR_ERR(priv->clk);

    priv->irq = platform_get_irq(pdev, 0);
    if (priv->irq < 0)
        return priv->irq;

    platform_set_drvdata(pdev, priv);
    dev_info(&pdev->dev, "probed successfully\n");
    return 0;
}

static const struct of_device_id mydev_of_match[] = {
    { .compatible = "vendor,mydevice" },
    { /* sentinel */ }
};
MODULE_DEVICE_TABLE(of, mydev_of_match);

static struct platform_driver mydev_driver = {
    .probe = mydev_probe,
    .driver = {
        .name = "mydevice",
        .of_match_table = mydev_of_match,
    },
};
module_platform_driver(mydev_driver);

MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("My Device Driver");
MODULE_AUTHOR("Author");
```

### 设备树节点示例

```dts
/ {
    mydevice@40000000 {
        compatible = "vendor,mydevice";
        reg = <0x40000000 0x1000>;
        interrupts = <GIC_SPI 42 IRQ_TYPE_LEVEL_HIGH>;
        clocks = <&cru CLK_MYDEV>;
        clock-names = "core";
        pinctrl-names = "default";
        pinctrl-0 = <&mydev_pins>;
        status = "okay";
    };
};
```

### I2C 设备驱动模板

```c
static int myiic_probe(struct i2c_client *client)
{
    struct myiic_priv *priv;

    priv = devm_kzalloc(&client->dev, sizeof(*priv), GFP_KERNEL);
    if (!priv)
        return -ENOMEM;

    priv->regmap = devm_regmap_init_i2c(client, &myiic_regmap_config);
    if (IS_ERR(priv->regmap))
        return PTR_ERR(priv->regmap);

    i2c_set_clientdata(client, priv);
    return 0;
}

static const struct i2c_device_id myiic_id[] = {
    { "myiic", 0 },
    { }
};
MODULE_DEVICE_TABLE(i2c, myiic_id);

static const struct of_device_id myiic_of_match[] = {
    { .compatible = "vendor,myiic-sensor" },
    { }
};
MODULE_DEVICE_TABLE(of, myiic_of_match);

static struct i2c_driver myiic_driver = {
    .driver = {
        .name = "myiic",
        .of_match_table = myiic_of_match,
    },
    .probe = myiic_probe,
    .id_table = myiic_id,
};
module_i2c_driver(myiic_driver);
```

### Yocto 层配方模板（.bb）

```bitbake
SUMMARY = "My custom kernel module"
LICENSE = "GPL-2.0-only"
LIC_FILES_CHKSUM = "file://COPYING;md5=..."

inherit module

SRC_URI = "file://mydriver.c \
           file://Makefile \
           "

S = "${WORKDIR}"

RPROVIDES:${PN} += "kernel-module-mydriver"
```
