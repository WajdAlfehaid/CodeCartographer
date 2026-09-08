// Report entity class

import java.util.Date;

public class Report {
    private String reportId;
    private String reportType;
    private Object data;
    private Date generatedAt;
    private String format;
    
    public Report() {
    }
    
    public String getReportId() {
        return reportId;
    }
    
    public void setReportId(String reportId) {
        this.reportId = reportId;
    }
    
    public String getReportType() {
        return reportType;
    }
    
    public void setReportType(String reportType) {
        this.reportType = reportType;
    }
    
    public Object getData() {
        return data;
    }
    
    public void setData(Object data) {
        this.data = data;
    }
    
    public Date getGeneratedAt() {
        return generatedAt;
    }
    
    public void setGeneratedAt(Date generatedAt) {
        this.generatedAt = generatedAt;
    }
    
    public String getFormat() {
        return format;
    }
    
    public void setFormat(String format) {
        this.format = format;
    }
}