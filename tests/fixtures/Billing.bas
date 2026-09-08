' VB6 billing module -- migrated from Access 97
Option Explicit

Public Const DISCOUNT_THRESHOLD As Double = 500
Private mTaxRate As Double

Public Function CalculateInvoiceTotal(ByVal subtotal As Double, ByVal qty As Integer) As Double
    Dim discount As Double
    discount = 0
    If subtotal > DISCOUNT_THRESHOLD Then
        discount = subtotal * 0.1
    ElseIf subtotal > 100 Then
        discount = subtotal * 0.05
    End If
    CalculateInvoiceTotal = (subtotal - discount) * (1 + GetTaxRate())
End Function

Private Function GetTaxRate() As Double
    GetTaxRate = 0.2
End Function

Public Sub PrintInvoice(ByVal total As Double)
    On Error Resume Next
    Debug.Print "Total: " & total
End Sub

Private Sub ObsoleteHelper()
    Dim i As Integer
    i = 0
End Sub
