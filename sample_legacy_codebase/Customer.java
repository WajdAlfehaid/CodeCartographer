// Customer entity class

import java.util.Date;

public class Customer {
    private String id;
    private String name;
    private String email;
    private String type;
    private double totalPurchases;
    private String status;
    private Date createdAt;
    private Date updatedAt;
    private double discountRate;
    
    public Customer() {
    }
    
    public Customer(String name, String email, String type) {
        this.name = name;
        this.email = email;
        this.type = type;
        this.totalPurchases = 0;
        this.status = "Active";
    }
    
    public String getId() {
        return id;
    }
    
    public void setId(String id) {
        this.id = id;
    }
    
    public String getName() {
        return name;
    }
    
    public void setName(String name) {
        this.name = name;
    }
    
    public String getEmail() {
        return email;
    }
    
    public void setEmail(String email) {
        this.email = email;
    }
    
    public String getType() {
        return type;
    }
    
    public void setType(String type) {
        this.type = type;
    }
    
    public double getTotalPurchases() {
        return totalPurchases;
    }
    
    public void setTotalPurchases(double totalPurchases) {
        this.totalPurchases = totalPurchases;
    }
    
    public String getStatus() {
        return status;
    }
    
    public void setStatus(String status) {
        this.status = status;
    }
    
    public Date getCreatedAt() {
        return createdAt;
    }
    
    public void setCreatedAt(Date createdAt) {
        this.createdAt = createdAt;
    }
    
    public Date getUpdatedAt() {
        return updatedAt;
    }
    
    public void setUpdatedAt(Date updatedAt) {
        this.updatedAt = updatedAt;
    }
    
    public double getDiscountRate() {
        return discountRate;
    }
    
    public void setDiscountRate(double discountRate) {
        this.discountRate = discountRate;
    }
}