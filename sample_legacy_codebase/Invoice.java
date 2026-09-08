// Invoice entity class

import java.util.Date;

public class Invoice {
    private String invoiceId;
    private String customerId;
    private double amount;
    private String description;
    private Date issueDate;
    private Date dueDate;
    private Date paidDate;
    private String status;
    private double lateFee;
    private double interest;
    private double totalDue;
    private double paymentAmount;
    
    public Invoice() {
    }
    
    public String getInvoiceId() {
        return invoiceId;
    }
    
    public void setInvoiceId(String invoiceId) {
        this.invoiceId = invoiceId;
    }
    
    public String getCustomerId() {
        return customerId;
    }
    
    public void setCustomerId(String customerId) {
        this.customerId = customerId;
    }
    
    public double getAmount() {
        return amount;
    }
    
    public void setAmount(double amount) {
        this.amount = amount;
    }
    
    public String getDescription() {
        return description;
    }
    
    public void setDescription(String description) {
        this.description = description;
    }
    
    public Date getIssueDate() {
        return issueDate;
    }
    
    public void setIssueDate(Date issueDate) {
        this.issueDate = issueDate;
    }
    
    public Date getDueDate() {
        return dueDate;
    }
    
    public void setDueDate(Date dueDate) {
        this.dueDate = dueDate;
    }
    
    public Date getPaidDate() {
        return paidDate;
    }
    
    public void setPaidDate(Date paidDate) {
        this.paidDate = paidDate;
    }
    
    public String getStatus() {
        return status;
    }
    
    public void setStatus(String status) {
        this.status = status;
    }
    
    public double getLateFee() {
        return lateFee;
    }
    
    public void setLateFee(double lateFee) {
        this.lateFee = lateFee;
    }
    
    public double getInterest() {
        return interest;
    }
    
    public void setInterest(double interest) {
        this.interest = interest;
    }
    
    public double getTotalDue() {
        return totalDue;
    }
    
    public void setTotalDue(double totalDue) {
        this.totalDue = totalDue;
    }
    
    public double getPaymentAmount() {
        return paymentAmount;
    }
    
    public void setPaymentAmount(double paymentAmount) {
        this.paymentAmount = paymentAmount;
    }
}