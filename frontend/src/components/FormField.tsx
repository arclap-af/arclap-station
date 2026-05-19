import type { InputHTMLAttributes, ReactNode, SelectHTMLAttributes } from "react";

interface FormFieldProps {
  label?: ReactNode;
  hint?: ReactNode;
  children: ReactNode;
  className?: string;
}

export function FormField({ label, hint, children, className = "" }: FormFieldProps) {
  return (
    <div className={`as-field ${className}`}>
      {label && <label className="as-field-label">{label}</label>}
      {children}
      {hint && <div className="as-field-hint">{hint}</div>}
    </div>
  );
}

export function TextInput(props: InputHTMLAttributes<HTMLInputElement>) {
  const { className = "", ...rest } = props;
  return <input className={`as-input ${className}`.trim()} {...rest} />;
}

export function Select(props: SelectHTMLAttributes<HTMLSelectElement>) {
  const { className = "", ...rest } = props;
  return <select className={`as-select ${className}`.trim()} {...rest} />;
}
