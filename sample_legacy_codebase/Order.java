// Order entity class

import java.util.Date;
import java.util.ArrayList;

public class Order {
    private String orderId;
    private String customerId;
    private ArrayList<OrderItem> items;
    private Date orderDate;
    private Date updatedAt;
    private String status;
    private double subtotal;
    private double tax;
    private double shipping;
    private double total;
    
    public Order() {
    }
    
    public String getOrderId() {
        return orderId;
    }
    
    public void setOrderId(String orderId) {
        this.orderId = orderId;
    }
    
    public String getCustomerId() {
        return customerId;
    }
    
    public void setCustomerId(String customerId) {
        this.customerId = customerId;
    }
    
    public ArrayList<OrderItem> getItems() {
        return items;
    }
    
    public void setItems(ArrayList<OrderItem> items) {
        this.items = items;
    }
    
    public Date getOrderDate() {
        return orderDate;
    }
    
    public void setOrderDate(Date orderDate) {
        this.orderDate = orderDate;
    }
    
    public Date getUpdatedAt() {
        return updatedAt;
    }
    
    public void setUpdatedAt(Date updatedAt) {
        this.updatedAt = updatedAt;
    }
    
    public String getStatus() {
        return status;
    }
    
    public void setStatus(String status) {
        this.status = status;
    }
    
    public double getSubtotal() {
        return subtotal;
    }
    
    public void setSubtotal(double subtotal) {
        this.subtotal = subtotal;
    }
    
    public double getTax() {
        return tax;
    }
    
    public void setTax(double tax) {
        this.tax = tax;
    }
    
    public double getShipping() {
        return shipping;
    }
    
    public void setShipping(double shipping) {
        this.shipping = shipping;
    }
    
    public double getTotal() {
        return total;
    }
    
    public void setTotal(double total) {
        this.total = total;
    }
}