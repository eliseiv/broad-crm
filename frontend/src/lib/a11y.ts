/**
 * Композиция `aria-describedby` для полей формы (08-design-system.md «Подсказка под полем формы
 * связывается с контролом», TD-061).
 *
 * Атрибут собирается из id подсказки **И** id ошибки — пробел-разделённым списком в порядке
 * «подсказка, затем ошибка», а не «или»: подсказка не исчезает из описания при появлении ошибки,
 * ошибка не вытесняет подсказку. Если нет ни того, ни другого — атрибут НЕ выводится
 * (`undefined`): висячий IDREF запрещён.
 */
export function composeDescribedBy(
  ...ids: (string | false | null | undefined)[]
): string | undefined {
  const present = ids.filter((id): id is string => Boolean(id));
  return present.length > 0 ? present.join(' ') : undefined;
}
