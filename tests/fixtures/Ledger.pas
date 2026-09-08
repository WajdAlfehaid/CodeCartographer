unit Ledger;
{ Delphi 5 accounting unit. Original author left in 2003. }
interface
uses SysUtils, Classes;

type
  TLedger = class(TObject)
  private
    FBalance: Double;
  public
    function ApplyInterest(Rate: Double): Double;
    procedure PostEntry(Amount: Double);
  end;

implementation

function TLedger.ApplyInterest(Rate: Double): Double;
begin
  if Rate > 0.15 then
    Rate := 0.15;
  FBalance := FBalance * (1 + Rate);
  Result := FBalance;
end;

procedure TLedger.PostEntry(Amount: Double);
begin
  FBalance := FBalance + Amount;
end;

procedure UnusedDebugDump;
begin
  WriteLn('debug');
end;

end.
