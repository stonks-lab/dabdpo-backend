-- ============================================================
-- Tablero DAB-DPO — Objetos SQL Server
-- Base de datos: DAB (servidor: 10.18.18.77)
-- Ejecutar en SSMS o sqlcmd antes del primer ETL
-- ============================================================

USE [DAB];
GO

-- ──────────────────────────────────────────────────────────
-- Tabla: dbo.proyecciones
-- Fuente: P&C Power BI Service (ETL Python)
-- ──────────────────────────────────────────────────────────
IF NOT EXISTS (
    SELECT 1 FROM sys.tables t
    JOIN sys.schemas s ON t.schema_id = s.schema_id
    WHERE s.name = 'dbo' AND t.name = 'proyecciones'
)
BEGIN
    CREATE TABLE dbo.proyecciones (
        id               INT           IDENTITY(1,1) NOT NULL,
        contrato         NVARCHAR(100) NOT NULL,
        proyecto         NVARCHAR(300) NULL,
        periodo          NVARCHAR(7)   NOT NULL,      -- Formato YYYY-MM
        monto_proyectado DECIMAL(18,2) NULL,
        moneda           NVARCHAR(5)   NULL,           -- CLP, USD, UF...
        tipo_proyeccion  NVARCHAR(10)  NULL,           -- 'P&C' o 'AdC'
        fecha_carga      DATETIME      NOT NULL CONSTRAINT DF_proyecciones_fecha_carga DEFAULT GETDATE(),

        CONSTRAINT PK_proyecciones PRIMARY KEY CLUSTERED (id),
        CONSTRAINT UQ_proyecciones_key
            UNIQUE (contrato, proyecto, periodo, tipo_proyeccion)
    );

    CREATE NONCLUSTERED INDEX IX_proyecciones_periodo
        ON dbo.proyecciones (periodo);

    CREATE NONCLUSTERED INDEX IX_proyecciones_contrato
        ON dbo.proyecciones (contrato);

    CREATE NONCLUSTERED INDEX IX_proyecciones_contrato_periodo
        ON dbo.proyecciones (contrato, periodo)
        INCLUDE (monto_proyectado, tipo_proyeccion);

    PRINT '✓ Tabla dbo.proyecciones creada';
END
ELSE
    PRINT '  Tabla dbo.proyecciones ya existe';
GO

-- ──────────────────────────────────────────────────────────
-- Vista: v_proyecciones_resumen
-- Agrega por contrato/proyecto/tipo para el tablero
-- ──────────────────────────────────────────────────────────
CREATE OR ALTER VIEW dbo.v_proyecciones_resumen AS
SELECT
    contrato,
    proyecto,
    periodo,
    tipo_proyeccion,
    SUM(monto_proyectado)  AS monto_proyectado,
    MAX(moneda)            AS moneda,
    MAX(fecha_carga)       AS fecha_carga
FROM dbo.proyecciones
GROUP BY contrato, proyecto, periodo, tipo_proyeccion;
GO

-- ──────────────────────────────────────────────────────────
-- Vista: v_proyecciones_vs_ep
-- Cruza proyecciones con EP real — útil para reportes y apps
-- ──────────────────────────────────────────────────────────
CREATE OR ALTER VIEW dbo.v_proyecciones_vs_ep AS
WITH ep_agg AS (
    SELECT
        codigo_ctt                                  AS contrato,
        Proyecto                                    AS proyecto,
        -- Normaliza período YYYY-MM desde MES/AÑO
        FORMAT(
            DATEFROMPARTS(
                TRY_CAST([AÑO] AS INT),
                CASE LOWER(MES)
                    WHEN 'enero'      THEN 1  WHEN 'febrero'    THEN 2
                    WHEN 'marzo'      THEN 3  WHEN 'abril'      THEN 4
                    WHEN 'mayo'       THEN 5  WHEN 'junio'      THEN 6
                    WHEN 'julio'      THEN 7  WHEN 'agosto'     THEN 8
                    WHEN 'septiembre' THEN 9  WHEN 'octubre'    THEN 10
                    WHEN 'noviembre'  THEN 11 WHEN 'diciembre'  THEN 12
                    ELSE NULL
                END, 1
            ), 'yyyy-MM'
        )                                           AS periodo,
        SUM(
            CASE WHEN ExchageRate IS NULL OR ExchageRate = 0
                 THEN monto_consumido
                 ELSE monto_consumido * ExchageRate
            END
        )                                           AS ep_consumido_clp
    FROM dbo.ep_detalle
    GROUP BY codigo_ctt, Proyecto, [AÑO], MES
)
SELECT
    COALESCE(p.contrato, e.contrato)           AS contrato,
    COALESCE(p.proyecto, e.proyecto)           AS proyecto,
    COALESCE(p.periodo,  e.periodo)            AS periodo,
    COALESCE(p.monto_proyectado, 0)            AS monto_proyectado,
    COALESCE(p.tipo_proyeccion, 'P&C')         AS tipo_proyeccion,
    COALESCE(e.ep_consumido_clp, 0)            AS ep_consumido_clp,
    COALESCE(p.monto_proyectado, 0)
        - COALESCE(e.ep_consumido_clp, 0)      AS diferencia_clp
FROM dbo.proyecciones p
FULL OUTER JOIN ep_agg e
    ON  p.contrato = e.contrato
    AND ISNULL(p.proyecto, '') = ISNULL(e.proyecto, '')
    AND p.periodo  = e.periodo;
GO

PRINT '✓ Vistas creadas: v_proyecciones_resumen, v_proyecciones_vs_ep';
GO

-- ──────────────────────────────────────────────────────────
-- Tabla: dbo.sourcing_plan
-- Fuente: Excel OneDrive (ETL Python) — formato largo
-- Una fila por (proyecto × mes)
-- ──────────────────────────────────────────────────────────
IF NOT EXISTS (
    SELECT 1 FROM sys.tables t
    JOIN sys.schemas s ON t.schema_id = s.schema_id
    WHERE s.name = 'dbo' AND t.name = 'sourcing_plan'
)
BEGIN
    CREATE TABLE dbo.sourcing_plan (
        id             INT           IDENTITY(1,1) NOT NULL,
        estatus        NVARCHAR(100) NULL,
        proyecto       NVARCHAR(300) NULL,
        id_blanco      NVARCHAR(100) NULL,
        programa       NVARCHAR(100) NULL,
        tipo_fin       NVARCHAR(100) NULL,
        cliente        NVARCHAR(200) NULL,
        contrato       NVARCHAR(100) NULL,
        empresa        NVARCHAR(200) NULL,
        lic            NVARCHAR(100) NULL,
        tipo           NVARCHAR(50)  NULL,
        inicio         DATE          NULL,
        termino        DATE          NULL,
        alcance        NVARCHAR(500) NULL,
        cantidad_total DECIMAL(18,2) NULL,
        periodo        NVARCHAR(7)   NOT NULL,
        cantidad_plan  DECIMAL(18,2) NULL,
        activo         BIT           NULL,
        fecha_carga    DATETIME      NOT NULL CONSTRAINT DF_sourcing_plan_fecha_carga DEFAULT GETDATE(),

        CONSTRAINT PK_sourcing_plan PRIMARY KEY CLUSTERED (id)
    );

    CREATE NONCLUSTERED INDEX IX_sourcing_plan_contrato_periodo
        ON dbo.sourcing_plan (contrato, periodo);

    CREATE NONCLUSTERED INDEX IX_sourcing_plan_proyecto
        ON dbo.sourcing_plan (proyecto);

    PRINT '✓ Tabla dbo.sourcing_plan creada';
END
ELSE
    PRINT '  Tabla dbo.sourcing_plan ya existe';
GO
