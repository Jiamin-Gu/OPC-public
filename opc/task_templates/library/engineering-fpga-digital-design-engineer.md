## 技术交付物

### AXI4-Lite 从设备模板（SystemVerilog）

```systemverilog
module axi_lite_slave #(
    parameter ADDR_WIDTH = 8,
    parameter DATA_WIDTH = 32
)(
    input  logic                    aclk,
    input  logic                    aresetn,
    // Write address
    input  logic [ADDR_WIDTH-1:0]   s_axi_awaddr,
    input  logic                    s_axi_awvalid,
    output logic                    s_axi_awready,
    // Write data
    input  logic [DATA_WIDTH-1:0]   s_axi_wdata,
    input  logic [DATA_WIDTH/8-1:0] s_axi_wstrb,
    input  logic                    s_axi_wvalid,
    output logic                    s_axi_wready,
    // Write response
    output logic [1:0]              s_axi_bresp,
    output logic                    s_axi_bvalid,
    input  logic                    s_axi_bready,
    // Read address
    input  logic [ADDR_WIDTH-1:0]   s_axi_araddr,
    input  logic                    s_axi_arvalid,
    output logic                    s_axi_arready,
    // Read data
    output logic [DATA_WIDTH-1:0]   s_axi_rdata,
    output logic [1:0]              s_axi_rresp,
    output logic                    s_axi_rvalid,
    input  logic                    s_axi_rready
);

    localparam NUM_REGS = 2**(ADDR_WIDTH-2);
    logic [DATA_WIDTH-1:0] regs [NUM_REGS];

    // Write logic
    always_ff @(posedge aclk or negedge aresetn) begin
        if (!aresetn) begin
            s_axi_awready <= 1'b0;
            s_axi_wready  <= 1'b0;
            s_axi_bvalid  <= 1'b0;
            s_axi_bresp   <= 2'b00;
        end else begin
            if (s_axi_awvalid && s_axi_wvalid && !s_axi_bvalid) begin
                s_axi_awready <= 1'b1;
                s_axi_wready  <= 1'b1;
                regs[s_axi_awaddr[ADDR_WIDTH-1:2]] <= s_axi_wdata;
                s_axi_bvalid  <= 1'b1;
            end else begin
                s_axi_awready <= 1'b0;
                s_axi_wready  <= 1'b0;
                if (s_axi_bvalid && s_axi_bready)
                    s_axi_bvalid <= 1'b0;
            end
        end
    end

    // Read logic
    always_ff @(posedge aclk or negedge aresetn) begin
        if (!aresetn) begin
            s_axi_arready <= 1'b0;
            s_axi_rvalid  <= 1'b0;
            s_axi_rresp   <= 2'b00;
        end else begin
            if (s_axi_arvalid && !s_axi_rvalid) begin
                s_axi_arready <= 1'b1;
                s_axi_rdata   <= regs[s_axi_araddr[ADDR_WIDTH-1:2]];
                s_axi_rvalid  <= 1'b1;
            end else begin
                s_axi_arready <= 1'b0;
                if (s_axi_rvalid && s_axi_rready)
                    s_axi_rvalid <= 1'b0;
            end
        end
    end

endmodule
```

### 异步 FIFO 核心逻辑

```systemverilog
// 写指针同步到读时钟域
always_ff @(posedge rd_clk or negedge rd_rstn) begin
    if (!rd_rstn) begin
        wr_ptr_gray_sync1 <= '0;
        wr_ptr_gray_sync2 <= '0;
    end else begin
        wr_ptr_gray_sync1 <= wr_ptr_gray;
        wr_ptr_gray_sync2 <= wr_ptr_gray_sync1;
    end
end

assign empty = (rd_ptr_gray == wr_ptr_gray_sync2);
assign full  = (wr_ptr_gray == {~rd_ptr_gray_sync2[ADDR_W:ADDR_W-1],
                                 rd_ptr_gray_sync2[ADDR_W-2:0]});
```

### Vivado 约束文件模板（.xdc）

```tcl
# 主时钟
create_clock -period 10.000 -name sys_clk [get_ports sys_clk_p]

# 跨时钟域 false path
set_false_path -from [get_clocks clk_a] -to [get_clocks clk_b]

# I/O 延迟
set_input_delay -clock sys_clk -max 3.0 [get_ports data_in[*]]
set_input_delay -clock sys_clk -min 1.0 [get_ports data_in[*]]
set_output_delay -clock sys_clk -max 2.5 [get_ports data_out[*]]
```
