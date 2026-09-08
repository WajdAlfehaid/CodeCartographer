C     Heat transfer solver, ported from punch cards in 1987
      PROGRAM HEATSIM
      REAL TEMP(100)
      CALL INITGRID(TEMP)
      CALL SOLVE(TEMP, 100)
      STOP
      END

      SUBROUTINE INITGRID(T)
      REAL T(100)
      DO 10 I = 1, 100
         T(I) = 0.0
   10 CONTINUE
      END SUBROUTINE

      SUBROUTINE SOLVE(T, N)
      REAL T(N)
      INTEGER N
      DO 20 K = 1, 500
         DO 30 I = 2, N-1
            IF (T(I) .GT. 100.0 .AND. K .GT. 10) THEN
               T(I) = 100.0
            ENDIF
   30    CONTINUE
   20 CONTINUE
      END SUBROUTINE

      SUBROUTINE DEADROUTINE(X)
      REAL X
      X = 0.0
      END SUBROUTINE
