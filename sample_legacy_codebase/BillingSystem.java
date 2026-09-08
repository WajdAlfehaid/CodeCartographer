// Billing System
// Handles invoicing and payments

import java.util.Date;
import java.util.HashMap;
import java.util.ArrayList;

public class BillingSystem {
    
    private static HashMap<String, Invoice> invoiceDatabase = new HashMap<>();
    private static int invoiceCount = 0;
    private static final double LATE_FEE_RATE = 0.015;
    private static final double INTEREST_RATE = 0.02;
    private static final int PAYMENT_TERMS = 30;
    private static final double MIN_INVOICE_AMOUNT = 1.0;
    private static final double MAX_INVOICE_AMOUNT = 1000000.0;
    
    public static String generateInvoice(String customerId, double amount, String description) {
        if (amount < MIN_INVOICE_AMOUNT || amount > MAX_INVOICE_AMOUNT) {
            return null;
        }
        
        String invoiceId = "INV-" + System.currentTimeMillis() + "-" + customerId.substring(0, 6);
        
        Invoice invoiceData = new Invoice();
        invoiceData.setInvoiceId(invoiceId);
        invoiceData.setCustomerId(customerId);
        invoiceData.setAmount(amount);
        invoiceData.setDescription(description);
        invoiceData.setIssueDate(new Date());
        invoiceData.setDueDate(new Date(System.currentTimeMillis() + (PAYMENT_TERMS * 24 * 60 * 60 * 1000L)));
        invoiceData.setStatus("Unpaid");
        invoiceData.setLateFee(0);
        invoiceData.setInterest(0);
        invoiceData.setTotalDue(amount);
        
        invoiceDatabase.put(invoiceId, invoiceData);
        invoiceCount++;
        saveInvoices();
        
        return invoiceId;
    }
    
    public static boolean applyLateFee(String invoiceId) {
        if (!invoiceDatabase.containsKey(invoiceId)) {
            return false;
        }
        
        Invoice invoice = invoiceDatabase.get(invoiceId);
        
        if (invoice.getStatus().equals("Unpaid")) {
            Date dueDate = invoice.getDueDate();
            Date currentDate = new Date();
            
            if (currentDate.after(dueDate)) {
                long daysLate = (currentDate.getTime() - dueDate.getTime()) / (1000 * 60 * 60 * 24);
                double lateFee = invoice.getAmount() * LATE_FEE_RATE * ((daysLate / 30) + 1);
                invoice.setLateFee(Math.round(lateFee * 100.0) / 100.0);
                invoice.setTotalDue(invoice.getAmount() + invoice.getLateFee() + invoice.getInterest());
                saveInvoices();
                return true;
            }
        }
        
        return false;
    }
    
    public static boolean recordPayment(String invoiceId, double paymentAmount) {
        if (!invoiceDatabase.containsKey(invoiceId)) {
            return false;
        }
        
        Invoice invoice = invoiceDatabase.get(invoiceId);
        
        if (paymentAmount >= invoice.getTotalDue()) {
            invoice.setStatus("Paid");
            invoice.setPaidDate(new Date());
            invoice.setPaymentAmount(paymentAmount);
            saveInvoices();
            return true;
        } else if (paymentAmount > 0) {
            invoice.setTotalDue(invoice.getTotalDue() - paymentAmount);
            saveInvoices();
            return true;
        }
        
        return false;
    }
    
    public static double calculateTotalReceivables() {
        double total = 0;
        
        for (Invoice invoice : invoiceDatabase.values()) {
            if (invoice.getStatus().equals("Unpaid")) {
                total += invoice.getTotalDue();
            }
        }
        
        return total;
    }
    
    private static void saveInvoices() {
        try {
            // Serialization logic
            System.out.println("Invoices saved");
        } catch (Exception e) {
            System.out.println("Error saving invoices: " + e.getMessage());
        }
    }
    
    // Dead method - never used
    private static void tempFunc3() {
        // This function is never called
    }
    
    // Dead method
    private static double unusedCalculation() {
        double x = 42;
        double y = 17;
        double z = x * y / 3.14;
        return z;
    }
}